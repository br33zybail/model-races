"""Blind soft-dimension judge. Sees anonymized transcripts as Head-A/Head-B,
must cite evidence, escalates close calls to a second family (grok-shared).
Quality is execution-graded elsewhere; this scores ONLY the soft dims."""
import json
import os
import random
import re
import sys
import urllib.request

from . import config

GATEWAY = config.CHAT_URL
JUDGE = config.ROSTER["judge"]["primary"]       # off the racing families
JUDGE2 = config.ROSTER["judge"]["secondary"]    # different lab entirely; close-call escalation
TELLS = re.compile(
    r"\b(omp|claude code|claude|anthropic|fable|opus|sonnet|haiku|assistant:)\b", re.I)
DIMS = ("autonomy", "verification", "synthesis")

RUBRIC = """You are grading two AI transcripts (Head-A, Head-B) that did the SAME task.
Score EACH head 0-3 on three axes. You MUST quote one short line of evidence per head.
- autonomy: completed without stalling/looping/asking for help it should resolve
- verification: did it actually VERIFY the result (ran the check) vs just claim done
- synthesis: signal-over-noise; concluded rather than dumped
Return ONLY JSON:
{"A":{"autonomy":n,"verification":n,"synthesis":n,"evidence":"..."},
 "B":{"autonomy":n,"verification":n,"synthesis":n,"evidence":"..."}}"""


def _call(model, prompt):
    body = {"model": model, "max_tokens": 16000,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        GATEWAY, json.dumps(body).encode(), config.headers())
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    return d["choices"][0]["message"]["content"] or ""


def _anon(t):
    return TELLS.sub("[X]", t or "")[:12000]


def _ask(model, a_txt, b_txt):
    prompt = f"{RUBRIC}\n\n=== Head-A ===\n{_anon(a_txt)}\n\n=== Head-B ===\n{_anon(b_txt)}"
    try:
        txt = _call(model, prompt)
    except Exception as ex:
        print(f"judge call failed ({model}): {ex}", file=sys.stderr)
        return None
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def _total(side):
    return sum(side.get(k, 0) for k in DIMS)


def judge_fixture(fixture, transcripts):
    lanes = list(transcripts)
    if len(lanes) < 2:
        return {l: {**{k: 0 for k in DIMS}, "evidence": "n/a"} for l in lanes}
    anchor = lanes[0]
    out = {}
    for other in lanes[1:]:
        flip = random.random() < 0.5
        a, b = (other, anchor) if flip else (anchor, other)
        r = _ask(JUDGE, transcripts[a], transcripts[b])
        primary_failed = r is None
        if not r:
            r = {"A": {}, "B": {}}
        second_ok = False
        if abs(_total(r.get("A", {})) - _total(r.get("B", {}))) <= 1:
            r2 = _ask(JUDGE2, transcripts[a], transcripts[b])
            if r2:
                second_ok = True
                for side in ("A", "B"):
                    for k in DIMS:
                        r[side][k] = round(
                            (r[side].get(k, 0) + r2.get(side, {}).get(k, 0)) / 2, 1)
                    r[side]["second_judge"] = True
        # Fail closed (grok review 2026-07-12): if no judge produced a real
        # verdict, mark it — all-zero dims from a transport failure must not
        # render as a confident "everyone scored 0" grade.
        judge_error = primary_failed and not second_ok
        amap, bmap = r.get("A", {}), r.get("B", {})
        out[a] = {**{k: amap.get(k, 0) for k in DIMS},
                  "evidence": amap.get("evidence", ""),
                  "second_judge": amap.get("second_judge", False),
                  "judge_error": judge_error}
        out[b] = {**{k: bmap.get(k, 0) for k in DIMS},
                  "evidence": bmap.get("evidence", ""),
                  "second_judge": bmap.get("second_judge", False),
                  "judge_error": judge_error}
    return out


if __name__ == "__main__":
    res = json.load(open(sys.argv[1]))
    by_fix = {}
    for r in res:
        txt = ""
        if r.get("transcript_path") and os.path.exists(r["transcript_path"]):
            txt = open(r["transcript_path"]).read()
        by_fix.setdefault(r["fixture"], {})[r["lane"]] = txt
    scores = {fx: judge_fixture(fx, t) for fx, t in by_fix.items()}
    out_path = sys.argv[1].replace("results.json", "judge.json")
    json.dump(scores, open(out_path, "w"), indent=2)
    print(f"{out_path} written")
