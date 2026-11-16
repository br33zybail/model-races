#!/usr/bin/env python3
"""Re-sample one tier N times per model and report a PASS RATE, not a single draw.

Why this exists: in back-to-back runs of the same tier at the same effort,
gpt-5.6-luna scored 11/11 and then 0/11. A one-shot race cannot distinguish
"this model is good" from "this sample was lucky". Kimi K2.5 fooled us the same
way (0/10 then 10/10) before it was demoted.

Usage:
  python3 -m model_races.resample <tier_index> <n> <model> [model...]
"""
import statistics
import sys

from . import tiers as race


def main():
    tier_idx, n = int(sys.argv[1]), int(sys.argv[2])
    models = sys.argv[3:]
    tier = race.TIERS[tier_idx]
    print(f"tier: {tier['name']}   samples: {n}   effort: {race.EFFORT}\n")
    spent = 0.0
    for m in models:
        scores, costs, lats, notes = [], [], [], []
        for _ in range(n):
            try:
                txt, dt, cost, tok, rt = race.call(m, tier["prompt"])
                spent += cost
                p, t, d = race.grade(race.extract_code(txt), tier["tests"])
                scores.append(p / t if t else 0.0)
                costs.append(cost); lats.append(dt)
                if d: notes.append(d[:34])
            except Exception as e:
                scores.append(0.0); notes.append(f"ERR {str(e)[:26]}")
        mean = statistics.mean(scores)
        worst = min(scores)
        perfect = sum(1 for s in scores if s == 1.0)
        name = m.split("/")[-1]
        print(f"  {name:18} mean={mean:5.0%}  worst={worst:5.0%}  perfect={perfect}/{n}  "
              f"${sum(costs):.3f}  {statistics.mean(lats) if lats else 0:5.1f}s")
        for note in notes[:2]:
            if note: print(f"      · {note}")
    print(f"\n  resample spend: ${spent:.2f}")


if __name__ == "__main__":
    main()
