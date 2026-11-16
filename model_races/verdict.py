"""Assemble the strengths map. Map, don't sum. 'tie' is a legal outcome."""
import json
import os
import sys
from collections import defaultdict

DIMS = ("autonomy", "verification", "synthesis")


def _winner(pairs, higher_better=True):
    # pairs: list of (lane, value). tie if top two within epsilon.
    if not pairs:
        return "n/a"
    s = sorted(pairs, key=lambda x: x[1], reverse=higher_better)
    if len(s) > 1 and abs(s[0][1] - s[1][1]) <= (0.1 if higher_better else 0.001):
        return "tie"
    return s[0][0]


def build_map(results, judge):
    by_fix = defaultdict(dict)
    for r in results:
        by_fix[r["fixture"]][r["lane"]] = r
    out = {}
    for fx, lanes in by_fix.items():
        # null scores = the rig failed on that lane (lane_error/grade_error);
        # exclude from winner math instead of letting them compete as zeros.
        q = [(l, lanes[l]["score"]) for l in lanes if lanes[l]["score"] is not None]
        rig_failed = [l for l in lanes if lanes[l]["score"] is None]
        j = judge.get(fx, {})
        judge_missing = (not j) or any(j.get(l, {}).get("judge_error") for l in lanes)
        softtot = (None if judge_missing else
                   {l: sum(j.get(l, {}).get(k, 0) for k in DIMS) for l in lanes})
        quality_winner = _winner(q)
        # confidence (fail closed, grok review 2026-07-12): "n/a" when the rig
        # itself failed anywhere in this fixture — a winner computed over
        # nulls/imputed zeros is authority without evidence. Otherwise: high
        # for a clear hard winner, or a hard tie with judges agreeing.
        if rig_failed or (quality_winner == "tie" and judge_missing):
            conf = "n/a"
        elif quality_winner != "tie":
            conf = "high"
        else:
            conf = "high" if len(set(softtot.values())) == 1 else "low"
        out[fx] = {
            "quality": {l: lanes[l]["score"] for l in lanes},
            "quality_winner": quality_winner,
            "rig_failed_lanes": rig_failed,
            "soft_total": softtot,
            "soft_winner": "n/a" if judge_missing else _winner(list(softtot.items())),
            "cost_usd": {l: lanes[l]["cost_usd"] for l in lanes},  # two-currency
            "tokens": {l: lanes[l].get("tokens", 0) for l in lanes},
            "wall_s": {l: lanes[l]["wall_s"] for l in lanes},
            "confidence": conf,
        }
    return out


def close_calls(results):
    by_fix = defaultdict(list)
    for r in results:
        if r["score"] is not None:      # rig failures can't be "close"
            by_fix[r["fixture"]].append(r["score"])
    return [fx for fx, sc in by_fix.items()
            if len(sc) > 1 and (max(sc) - sorted(sc)[-2]) <= 0.1]


def render(m, close):
    lines = ["# Harness Race (C) — Verdict Map\n",
             "Per-fixture, per-dimension. No composite score. n=1 unless resampled.\n"]
    for fx, d in sorted(m.items()):
        lines.append(f"## {fx}  (confidence: {d['confidence']})")
        lines.append(f"- quality: {d['quality']}  -> winner **{d['quality_winner']}**")
        lines.append(f"- soft: {d['soft_total']}  -> winner **{d['soft_winner']}**")
        lines.append(f"- cost $: {d['cost_usd']}   tokens: {d['tokens']}   wall s: {d['wall_s']}\n")
    if close:
        lines.append(f"\n**Close calls — resample before trusting the winner:** {close}")
        lines.append("Re-run: `ONLY_LANES=<lanes> python3 -m model_races.race_harness <FX>` (n=2) and re-judge.")
    return "\n".join(lines)


if __name__ == "__main__":
    base = os.path.dirname(os.path.abspath(sys.argv[1]))
    results = json.load(open(sys.argv[1]))
    jp = os.path.join(base, "judge.json")
    judge = json.load(open(jp)) if os.path.exists(jp) else {}
    m = build_map(results, judge)
    cc = close_calls(results)
    open(os.path.join(base, "verdict.md"), "w").write(render(m, cc))
    print(f"verdict.md written. close calls to resample: {cc or 'none'}")
