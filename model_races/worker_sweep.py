"""D-mode worker sweep: single-shot model × fixture matrix (model-to-shape map).

Workers get NO tools — each fixture is presented as a single completion task
and the returned artifact is extracted and execution-graded with the same
fixture graders the lane race uses. This measures the MODEL's fit per task
shape; orchestration ability is the lane race's job.

⚠ Run ONLY while no lane race is in flight: per-call cost comes from the
gateway spend-log time window and concurrent traffic would pollute it.

Usage:
  python3 -m model_races.worker_sweep                 # cloud roster, all fixtures
  python3 -m model_races.worker_sweep F2 F3           # subset of fixtures
  ROSTER=local python3 -m model_races.worker_sweep    # local pass (ollama direct/gateway)
  SWEEP_BUDGET=2.50 (default)                # $ cap, checked before each call
"""
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

from . import config
from .gw_spend import since as gw_since
from .sandbox import run_grader

FIX_DIR = str(config.ROOT / "fixtures" / "tasks")
GATEWAY = config.CHAT_URL
OLLAMA = config.OLLAMA_URL
BUDGET = float(os.environ.get("SWEEP_BUDGET", "2.50"))
MAXTOK = 16000          # reasoning models think past small caps and return empty
CALL_TIMEOUT = 900

# (label, gateway model) pairs come from the roster file; a "twin" label marks
# a cloud model that stands in for a local one.
CLOUD_ROSTER = [tuple(r) for r in config.ROSTER["sweep"]["cloud"]]
LOCAL_ROSTER = [tuple(r) for r in config.ROSTER["sweep"]["local"]]

# Line-anchored fences. Typed searches REQUIRE the language tag — with an
# optional tag, block1's closing fence + block2's opening fence match as one
# empty fence (cost a debugging round to find).
FENCE_TYPED = r"^```%s[ \t]*\n(.*?)^```"
FENCE_ANY = r"^```[\w-]*[ \t]*\n(.*?)^```"


def _seed_text(fixture, fname):
    return open(os.path.join(FIX_DIR, fixture, "seed", fname)).read()


def sweep_prompt(fixture):
    base = open(os.path.join(FIX_DIR, fixture, "prompt.md")).read()
    if fixture == "F1":
        return (base + "\n\nHere is webhook.py:\n```python\n"
                + _seed_text("F1", "webhook.py")
                + "```\n\nYou have no tools. Return the COMPLETE fixed webhook.py "
                  "in a single ```python fence. No other code fences.")
    if fixture == "F2":
        return (base + "\n\nHere is loglines.txt:\n```\n"
                + _seed_text("F2", "loglines.txt")
                + "```\n\nYou have no tools. Return ONLY the CSV rows in a single "
                  "``` fence. No other fences, no prose.")
    if fixture == "F3":
        files = "".join(
            f"\n--- {fn} ---\n```\n{_seed_text('F3', fn)}```\n"
            for fn in ("app.yaml", "auth.py", "sync.py"))
        return (base + "\n" + files
                + "\nYou have no tools. Return the complete findings.md content "
                  "in a single ```markdown fence. No other fences.")
    if fixture == "F4":
        return (base + "\n\nYou have no tools. Return plan.md in a ```markdown "
                  "fence, then ratelimit.py in a ```python fence — exactly two "
                  "fences in that order.")
    raise ValueError(fixture)


def extract(fixture, txt):
    """-> {filename: content} or None if the expected fences are missing.
    Empty/whitespace-only fences are NOT artifacts (an empty file graded as a
    submission is the fence-regex bug class all over again). Last fence wins
    by design: models emit the final corrected version last."""
    py = [f for f in re.findall(FENCE_TYPED % "python", txt, re.DOTALL | re.M) if f.strip()]
    md = [f for f in re.findall(FENCE_TYPED % "(?:markdown|md)", txt, re.DOTALL | re.M) if f.strip()]
    anyf = [f for f in re.findall(FENCE_ANY, txt, re.DOTALL | re.M) if f.strip()]
    if fixture == "F1":
        return {"webhook.py": py[-1]} if py else (
            {"webhook.py": anyf[-1]} if anyf else None)
    if fixture == "F2":
        cand = [f for f in anyf if re.search(r"^\s*\d+\s*,", f, re.M)]
        return {"out.csv": cand[-1]} if cand else None
    if fixture == "F3":
        pick = md[-1] if md else (anyf[-1] if anyf else (txt if txt.strip() else None))
        return {"findings.md": pick} if pick else None
    if fixture == "F4":
        if not py:
            return None
        return {"ratelimit.py": py[-1], "plan.md": md[-1] if md else ""}
    return None


def grade(fixture, files):
    """-> score float, or None on grader failure (fail closed: rig failure is
    never a model score — grok review 2026-07-12)."""
    with tempfile.TemporaryDirectory() as ws:
        for fn, content in (files or {}).items():
            open(os.path.join(ws, fn), "w").write(content)
        score, _ = run_grader(os.path.join(FIX_DIR, fixture, "grade.py"), ws)
    return score


def call_gateway(model, prompt):
    body = {"model": model, "max_tokens": MAXTOK,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        GATEWAY, json.dumps(body).encode(), config.headers())
    with urllib.request.urlopen(req, timeout=CALL_TIMEOUT) as r:
        d = json.load(r)
    u = d.get("usage") or {}
    return (d["choices"][0]["message"]["content"] or "",
            u.get("prompt_tokens", 0), u.get("completion_tokens", 0))


def call_ollama(model, prompt):
    # /api/generate by choice: it was the workaround for ollama 0.31.1's chat
    # endpoint swallowing gpt-oss:120b output (fixed in 0.31.2, verified), and
    # it remains correct for single-shot prompts on every local model.
    body = {"model": model, "stream": False, "prompt": prompt}
    req = urllib.request.Request(
        OLLAMA.replace("/api/chat", "/api/generate"),
        json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=CALL_TIMEOUT * 2) as r:
        d = json.load(r)
    return d.get("response", "")


def main():
    fixtures = [a for a in sys.argv[1:] if a.startswith("F")] or ["F1", "F2", "F3", "F4"]
    roster = LOCAL_ROSTER if os.environ.get("ROSTER") == "local" else CLOUD_ROSTER
    if os.environ.get("ROLES"):    # ad-hoc role race, e.g. ROLES="reasoner-fast,reasoner-fast-hi" — label = role name
        roster = [(r, r) for r in os.environ["ROLES"].split(",") if r]
    t0 = time.time()
    out_path = os.path.join(config.runs_dir(),
                            f"sweep-{time.strftime('%Y%m%d-%H%M%S')}.json")
    results = []
    for label, model in roster:
        for fx in fixtures:
            spent = gw_since(t0)
            if spent > BUDGET:
                print(f"SWEEP BUDGET STOP at ${spent:.2f} — remaining calls skipped")
                results.append({"model": label, "fixture": fx, "skipped": "budget"})
                continue
            t_call = time.time()
            tin = tout = 0
            try:
                if model.startswith("ollama:"):
                    txt = call_ollama(model.removeprefix("ollama:"), sweep_prompt(fx))
                else:
                    txt, tin, tout = call_gateway(model, sweep_prompt(fx))
                err = ""
            except Exception as ex:
                txt, err = "", f"{type(ex).__name__}: {ex}"[:200]
            wall = round(time.time() - t_call, 1)
            cost = 0.0 if model.startswith("ollama:") else gw_since(t_call)
            files = extract(fx, txt) if txt else None
            # score None = the RIG failed (grader crash), distinct from the
            # model failing (0.0) or shipping nothing (no_artifact).
            score = grade(fx, files) if files else 0.0
            gerr = "grader failed" if (files and score is None) else ""
            rec = {"model": label, "fixture": fx, "score": score,
                   "wall_s": wall, "cost_usd": round(cost, 4),
                   "tokens_in": tin, "tokens_out": tout,
                   "no_artifact": files is None, "error": err,
                   "grade_error": gerr}
            results.append(rec)
            s_disp = "GRADE-ERR" if score is None else f"{score:5.3f}"
            print(f"{label:42s} {fx}  score={s_disp}  {wall:6.1f}s  ${cost:.4f}"
                  f"{'  NO-ARTIFACT' if files is None else ''}{'  ERR '+err if err else ''}")
            json.dump(results, open(out_path, "w"), indent=2)
    print(f"\nsweep -> {out_path}   total gateway $: {gw_since(t0):.3f}")


if __name__ == "__main__":
    main()
