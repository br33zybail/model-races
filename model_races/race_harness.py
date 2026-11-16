"""C harness race: head lanes across the fixture set. Cloud-only, Fable at
every head. Reuses tiers.py's budget-cap discipline; captures cost per the
two-currency rule (omp -> gateway file-log; CC -> claude -p JSON).

Lanes:
  L1_cc_fable          claude -p, Fable head (subscription-covered; cost_usd is a proxy)
  L2_omp_fable         omp -p --model gateway/fable (retail $ via OpenRouter)
  L3_omp_cheap_escalate omp -p --model gateway/reasoner-free (free head)
  L3b_omp_toolcoder    omp -p --model gateway/tool-coder (cheap head)

Isolation: every head runs with an explicit minimal environment, cwd in its
per-run workspace and HOME in a fresh per-run directory beside it, so it
inherits none of your shell's keys, config, skills or MCP servers. The only
credential passed on is the one the lane needs: CLAUDE_CODE_OAUTH_TOKEN or
ANTHROPIC_API_KEY for L1, RACE_API_KEY (in a 0600 config file) for omp.
Heads get file tools only. RACE_HEAD_EXEC=1 also lets them run Python (L1) or
a shell (omp), which is code execution as your user: see README "Security".
"""
import json
import os
import shutil
import subprocess
import sys
import time

from . import config, sandbox
from .gw_spend import since as gw_since

FIX_DIR = str(config.ROOT / "fixtures" / "tasks")
LANE_ROLES = config.ROSTER["lanes"]
BUDGET = float(os.environ.get("BUDGET", "6.00"))
LANES = ["L1_cc_fable", "L2_omp_fable", "L3_omp_cheap_escalate", "L3b_omp_toolcoder"]
LANE_TIMEOUT_S = 1800
# est worst-case $ per run, used by the pre-call budget check
WORST_CASE = {"L1_cc_fable": 0.0, "L2_omp_fable": 2.0,
              "L3_omp_cheap_escalate": 0.2, "L3b_omp_toolcoder": 0.2}

# The fixtures need reading and writing files in the workspace, nothing more.
HEAD_EXEC = os.environ.get("RACE_HEAD_EXEC") == "1"
CC_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep"] + (["Bash"] if HEAD_EXEC else [])
OMP_TOOLS = ["read", "edit", "write", "grep", "glob"] + (["bash"] if HEAD_EXEC else [])
CC_AUTH_VARS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")


def _copy_seed(fixture, workspace):
    os.makedirs(workspace, exist_ok=True)
    seed = os.path.join(FIX_DIR, fixture, "seed")
    if os.path.isdir(seed):
        for fn in os.listdir(seed):
            shutil.copy(os.path.join(seed, fn), workspace)


def _prompt(fixture):
    return open(os.path.join(FIX_DIR, fixture, "prompt.md")).read()


def _grade(fixture, workspace):
    """-> (score|None, grade_error). Fail closed (grok review 2026-07-12):
    a grader crash/timeout/malformed output is None + reason, NEVER 0.0 —
    zeros must mean 'the model failed', not 'the rig failed'.
    The grader runs with a clean environment; graders that execute the
    submission do it through sandbox.py."""
    return sandbox.run_grader(os.path.join(FIX_DIR, fixture, "grade.py"), workspace)


def _lane_home(workspace):
    """A fresh HOME for one head run, beside (not inside) its workspace."""
    home = os.path.abspath(workspace) + ".home"
    shutil.rmtree(home, ignore_errors=True)
    os.makedirs(os.path.join(home, "tmp"))
    return home


def _lane_env(cli, home, extra=None):
    """Explicit minimal environment: nothing from the caller's shell except
    what `extra` names."""
    path = os.pathsep.join([os.path.dirname(cli), "/usr/local/bin", "/usr/bin", "/bin"])
    env = {"PATH": path, "HOME": home, "TMPDIR": os.path.join(home, "tmp"),
           "LANG": "C.UTF-8", "TERM": "dumb"}
    env.update(extra or {})
    return env


def _run_cc(prompt, workspace):
    """CC lane: headless Claude Code, Fable head. Cost from its JSON.

    Runs with HOME in a fresh directory, so it cannot read ~/.claude: it
    authenticates only from CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`) or
    ANTHROPIC_API_KEY in your environment, and the lane refuses to start
    without one. --restricted ignores settings files and confines the file
    tools to the workspace; --tools limits the head to file tools."""
    cli = shutil.which("claude")
    auth = {k: os.environ[k] for k in CC_AUTH_VARS if os.environ.get(k)}
    if not cli or not auth:
        why = ("claude not on PATH" if not cli else
               "set CLAUDE_CODE_OAUTH_TOKEN (claude setup-token) or "
               "ANTHROPIC_API_KEY; this lane does not read ~/.claude")
        return 0.0, 0, 0.0, f"LANE_ERROR: {why}"
    home = _lane_home(workspace)
    env = _lane_env(cli, home, {**auth, "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
                                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"})
    # Prompt goes via STDIN: --allowedTools is variadic and would swallow a
    # positional prompt argument (cost us a whole dead run to learn).
    cmd = [cli, "-p", "--model", LANE_ROLES["cc_model"], "--output-format", "json",
           "--restricted", "--tools", ",".join(CC_TOOLS),
           "--permission-mode", "acceptEdits", "--permission-prompts", "none",
           "--strict-mcp-config", "--no-session-persistence"]
    if HEAD_EXEC:
        # Opt-in: the head may run python to verify. Python is a full shell.
        cmd += ["--allowedTools", "Bash(python3:*)", "Bash(python:*)"]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=workspace, env=env, input=prompt,
                           capture_output=True, text=True, timeout=LANE_TIMEOUT_S)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    wall = time.time() - t0
    try:
        d = json.loads(p.stdout)
        usage = d.get("usage", {}) or {}
        tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        cost = float(d.get("total_cost_usd", 0.0) or 0.0)  # subscription: proxy, ~0
        txt = d.get("result", "") or p.stdout
        if d.get("is_error"):
            # e.g. "Not logged in": parseable JSON, but the head never worked.
            txt = "LANE_ERROR: claude -p reported an error\n" + txt
    except (ValueError, AttributeError):
        tokens, cost = 0, 0.0
        txt = "LANE_ERROR: claude -p produced no parseable JSON\n" + \
              (p.stdout or "") + (p.stderr or "")
    return wall, tokens, cost, txt


def _omp_config(home):
    """Gateway provider for omp, written 0600 into the lane's private HOME."""
    agent = os.path.join(home, ".omp", "agent")
    os.makedirs(agent)
    # JSON strings are valid YAML scalars, so json.dumps quotes safely.
    text = ("providers:\n  gateway:\n"
            f"    baseUrl: {json.dumps(config.BASE_URL)}\n"
            f"    apiKey: {json.dumps(config.API_KEY)}\n"
            "    api: openai-completions\n"
            "    discovery:\n      type: litellm\n")
    fd = os.open(os.path.join(agent, "models.yml"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _run_omp(prompt, workspace, head_role):
    """omp lane: head seat set via --model <role> (omp lists gateway roles
    unprefixed); $ from the gateway spend log during the run window.

    Same clean HOME as the CC lane, holding only a generated gateway config,
    so your own omp settings, skills and rules are not loaded. omp does not
    confine its file tools to the workspace: they can reach any path your
    user can. Run head lanes in a container."""
    cli = shutil.which("omp")
    if not cli:
        return 0.0, 0, 0.0, "LANE_ERROR: omp not on PATH"
    home = _lane_home(workspace)
    t_start = time.time()
    try:
        _omp_config(home)
        p = subprocess.run(
            [cli, "-p", "--no-session", "--model", head_role,
             "--tools", ",".join(OMP_TOOLS),
             "--no-extensions", "--no-skills", "--no-rules", "--no-title", prompt],
            cwd=workspace, env=_lane_env(cli, home), capture_output=True, text=True,
            timeout=LANE_TIMEOUT_S)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    wall = time.time() - t_start
    cost = gw_since(t_start)  # $ recorded by spend.jsonl in this window
    tokens = 0                # omp cost is $-primary; tokens not separately captured
    txt = (p.stdout or "") + (p.stderr or "")
    first = txt.lstrip().split("\n", 1)[0]
    # omp exits 0 when the model is unknown (seen on 16.3.11); without this the
    # untouched seed would be graded as if the head had worked.
    if p.returncode != 0 or (first.startswith('Model "') and "not found" in first):
        txt = f"LANE_ERROR: omp failed (rc={p.returncode})\n" + txt
    return wall, tokens, cost, txt


def run_lane(lane, fixture, workspace):
    # Heads must not read this process's environment (your keys) through
    # /proc/<ppid>/environ; omp's file tools are not confined to the workspace.
    sandbox.harden_caller()
    _copy_seed(fixture, workspace)
    prompt = _prompt(fixture)
    if os.environ.get("DRYRUN") == "1":
        # stub: prove isolation + result shape without spending
        open(os.path.join(workspace, "_dry"), "w").write("dry")
        return {"lane": lane, "fixture": fixture, "score": 0.0, "wall_s": 0.0,
                "tokens": 0, "cost_usd": 0.0, "transcript_path": "", "tool_loops": 0}
    if lane == "L1_cc_fable":
        wall, tokens, cost, txt = _run_cc(prompt, workspace)
    elif lane == "L2_omp_fable":
        wall, tokens, cost, txt = _run_omp(prompt, workspace, LANE_ROLES["omp_l2"])
    elif lane == "L3_omp_cheap_escalate":
        wall, tokens, cost, txt = _run_omp(prompt, workspace, LANE_ROLES["omp_l3"])
    elif lane == "L3b_omp_toolcoder":
        # cheap head that is ALSO the cloud twin of a local coder model
        wall, tokens, cost, txt = _run_omp(prompt, workspace, LANE_ROLES["omp_l3b"])
    else:
        raise ValueError(lane)
    tp = os.path.join(workspace, "_transcript.txt")
    open(tp, "w").write(txt or "")
    lane_error = (txt or "").startswith("LANE_ERROR")
    if lane_error:
        # Dead lane: grading the untouched seed would score the SEED, not the
        # model. score stays None so downstream can't mistake it for "tried
        # and failed".
        score, gerr = None, "lane_error: head never ran"
    else:
        score, gerr = _grade(fixture, workspace)
    return {"lane": lane, "fixture": fixture, "score": score, "wall_s": round(wall, 1),
            "tokens": tokens, "cost_usd": round(cost, 4), "transcript_path": tp,
            "tool_loops": (txt or "").count("tool_use"),
            "lane_error": lane_error, "grade_error": gerr}


def main():
    fixtures = sys.argv[1:] or ["F1", "F2", "F3", "F4"]
    lanes = [l for l in LANES
             if l in os.environ.get("ONLY_LANES", ",".join(LANES)).split(",")]
    run_dir = os.path.join(config.runs_dir(), time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)
    t_run_start = time.time()
    results = []
    for fx in fixtures:
        for lane in lanes:
            spent = gw_since(t_run_start)
            worst = WORST_CASE[lane]
            if spent + worst > BUDGET:
                print(f"BUDGET STOP: spent ${spent:.2f} + est ${worst:.2f} > ${BUDGET:.2f}"
                      f" — skipping {lane}/{fx}")
                continue
            ws = os.path.join(run_dir, f"{fx}_{lane}")
            print(f"running {lane}/{fx} ...")
            try:
                r = run_lane(lane, fx, ws)
            except subprocess.TimeoutExpired:
                r = {"lane": lane, "fixture": fx, "score": None,
                     "wall_s": LANE_TIMEOUT_S, "tokens": 0,
                     "cost_usd": round(gw_since(t_run_start) - spent, 4),
                     "transcript_path": "", "tool_loops": 0, "timeout": True,
                     "lane_error": True, "grade_error": "lane timeout"}
            results.append(r)
            print(f"  score={r['score']} wall={r['wall_s']}s cost=${r['cost_usd']}")
    json.dump(results, open(os.path.join(run_dir, "results.json"), "w"), indent=2)
    print(f"\nresults -> {run_dir}/results.json"
          f"   gateway spend this run: ${gw_since(t_run_start):.2f}")


if __name__ == "__main__":
    main()
