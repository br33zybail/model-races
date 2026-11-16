#!/usr/bin/env python3
"""topology_race.py — race orchestration STRUCTURES with models held constant.

The model-race culture applied one level up (graph engineering layer 3,
2026-07-20): same fixture, same budget, different topologies. First target is
the F3 audit shape. Topologies:

  solo    one seat: reasoner-fast answers directly (yesterday's baseline path)
  verify  finder (reasoner-fast, openai) -> adversarial verifier from a
          DIFFERENT family (grok-shared, xai) that refutes/corrects, output graded
  swarm   three cheap finders (tool-fast, reasoner-fast, tool-glm) -> judge
          (reasoner-fast) merges, output graded

Usage:  python3 -m model_races.topology_race [N_RUNS] [FIXTURE]     (default 2 F3)
Results: printed table + runs/topology-race-<fixture>-<ts>.json
If RACE_LEDGER names a script, every seat logs an edge through it. n=1 is not a
verdict; n=2 is a first signal, promotion needs the model-race bar.
"""
import json
import os
import subprocess
import sys
import time

from . import config
from .worker_sweep import sweep_prompt, extract, grade, call_gateway

LEDGER = config.LEDGER
SEATS = config.ROSTER["topology"]


def log_edge(child, group, shape, note=""):
    if not LEDGER:
        return
    try:
        subprocess.run(
            ["python3", LEDGER, "add", "--child", child, "--group", group,
             "--shape", shape, "--parent", "topology-race", "--note", note],
            capture_output=True, timeout=10)
    except Exception:
        pass


def graded(fixture, txt):
    files = extract(fixture, txt) if txt else None
    if not files:
        return 0.0, "no_artifact"
    score = grade(fixture, files)
    return (score if score is not None else None), ("ok" if score is not None else "grade_error")


def t_solo(fixture, run):
    gid = f"TRsolo{run}"
    txt, _, tout = call_gateway(SEATS["solo"], sweep_prompt(fixture))
    log_edge(SEATS["solo"], gid, "audit-solo")
    score, status = graded(fixture, txt)
    return score, status, tout


def t_verify(fixture, run):
    gid = f"TRverify{run}"
    prompt = sweep_prompt(fixture)
    found, _, tout1 = call_gateway(SEATS["finder"], prompt)
    log_edge(SEATS["finder"], gid, "audit-verify", "finder seat")
    verify_prompt = (
        prompt
        + "\n\nA first reviewer produced the findings below. You are an adversarial "
          "verifier from a different model family: refute anything not supported by "
          "the provided files, add anything real the reviewer missed, and return the "
          "corrected COMPLETE findings.md in a single ```markdown fence. No other fences.\n\n"
          "--- first reviewer's findings ---\n" + (found or "(none)"))
    corrected, _, tout2 = call_gateway(SEATS["verifier"], verify_prompt)
    log_edge(SEATS["verifier"], gid, "audit-verify", "adversarial verifier seat")
    score, status = graded(fixture, corrected)
    return score, status, tout1 + tout2


def t_swarm(fixture, run):
    gid = f"TRswarm{run}"
    prompt = sweep_prompt(fixture)
    outs, tokens = [], 0
    for role in SEATS["swarm_finders"]:
        try:
            txt, _, tout = call_gateway(role, prompt)
            tokens += tout
            outs.append((role, txt))
            # swarm seats are coverage, not an independence panel: no --group,
            # or the violations audit correctly-but-uselessly flags them.
            log_edge(role, "", "audit-swarm", f"finder seat {gid}")
        except Exception as ex:
            outs.append((role, f"(seat failed: {ex})"))
    judge_prompt = (
        prompt
        + "\n\nThree independent reviewers produced the findings below. Merge them: "
          "keep findings supported by the provided files, drop unsupported ones, dedupe, "
          "and return the final COMPLETE findings.md in a single ```markdown fence. "
          "No other fences.\n\n"
        + "\n\n".join(f"--- reviewer {i+1} ({r}) ---\n{t or '(none)'}"
                      for i, (r, t) in enumerate(outs)))
    merged, _, tout = call_gateway(SEATS["swarm_judge"], judge_prompt)
    tokens += tout
    log_edge(SEATS["swarm_judge"], "", "audit-swarm", f"judge seat {gid}")
    score, status = graded(fixture, merged)
    return score, status, tokens


TOPOLOGIES = [("solo", t_solo), ("verify", t_verify), ("swarm", t_swarm)]


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    fixture = sys.argv[2] if len(sys.argv) > 2 else "F3"
    results = []
    print(f"topology race: {fixture}, n={runs}")
    print(f"{'topology':10s} {'run':>3s} {'score':>7s} {'status':12s} {'tok_out':>8s} {'wall_s':>7s}")
    for name, fn in TOPOLOGIES:
        for run in range(1, runs + 1):
            t0 = time.time()
            try:
                score, status, tokens = fn(fixture, run)
            except Exception as ex:
                score, status, tokens = None, f"error:{type(ex).__name__}", 0
            wall = round(time.time() - t0, 1)
            s_disp = "RIG-ERR" if score is None else f"{score:.3f}"
            print(f"{name:10s} {run:3d} {s_disp:>7s} {status:12s} {tokens:8d} {wall:7.1f}")
            results.append({"topology": name, "run": run, "fixture": fixture,
                            "score": score, "status": status,
                            "tokens_out": tokens, "wall_s": wall})
    out = os.path.join(config.runs_dir(),
                       f"topology-race-{fixture}-{time.strftime('%Y%m%d-%H%M%S')}.json")
    json.dump(results, open(out, "w"), indent=2)
    print(f"\n-> {out}")
    by = {}
    for r in results:
        by.setdefault(r["topology"], []).append(r["score"])
    for name, scores in by.items():
        clean = [s for s in scores if s is not None]
        mean = sum(clean) / len(clean) if clean else 0.0
        print(f"{name}: scores={scores} mean={mean:.3f}")


if __name__ == "__main__":
    main()
