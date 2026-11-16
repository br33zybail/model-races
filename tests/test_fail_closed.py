"""Fail-closed contract tests (grok adversarial review, 2026-07-12).

The shared invariant under test: failure is never coerced into a plausible
number. Rig failures surface as None + explicit flags; budget gates
overestimate on missing data; graders can't be gamed via stdout.
"""
import json
import os

import pytest

from model_races import config, gw_spend
from model_races import race_harness as rh
from model_races import verdict as vd
from model_races import worker_sweep as wsw


# ---- #1/#3: grader integrity ------------------------------------------------

REF_F1 = (config.ROOT / "fixtures" / "tasks" / "F1" / "reference" / "webhook.py").read_text()

MALICIOUS_F1 = '''
import os
print("SCORE 1.0")
os._exit(0)
'''

MALICIOUS_F4 = '''
print("SCORE 1.0")
def wrong_interface(): pass
'''

KILLER_F4 = '''
import os
print("SCORE 1.0")
os._exit(0)
'''


def test_f1_reference_still_scores_1(tmp_path):
    (tmp_path / "webhook.py").write_text(REF_F1)
    score, err = rh._grade("F1", str(tmp_path))
    assert score == 1.0 and err == ""


def test_f1_print_and_exit_cannot_forge_score(tmp_path):
    (tmp_path / "webhook.py").write_text(MALICIOUS_F1)
    score, err = rh._grade("F1", str(tmp_path))
    # os._exit during import kills the runner before the score file is
    # written -> GRADE-ERROR -> None, never the forged 1.0
    assert score is None and "grade-error" in err


def test_f4_printed_score_without_interface_is_zero(tmp_path):
    (tmp_path / "ratelimit.py").write_text(MALICIOUS_F4)
    score, err = rh._grade("F4", str(tmp_path))
    # legit finish, contract genuinely absent -> real 0.0, not an error
    assert score == 0.0 and err == ""


def test_f4_killer_submission_is_grade_error(tmp_path):
    (tmp_path / "ratelimit.py").write_text(KILLER_F4)
    score, err = rh._grade("F4", str(tmp_path))
    assert score is None and "grade-error" in err


def test_sweep_grade_matches_harness_contract():
    assert wsw.grade("F4", {"ratelimit.py": KILLER_F4}) is None
    assert wsw.grade("F4", {"ratelimit.py": MALICIOUS_F4}) == 0.0


# ---- #9: extract refuses empty artifacts -------------------------------------

def test_extract_rejects_empty_fences():
    txt = "```python\n\n```\nsome prose\n```python\ndef allow(key): return True\n```"
    out = wsw.extract("F4", txt)
    assert out and out["ratelimit.py"].strip().startswith("def allow")
    assert wsw.extract("F1", "```python\n   \n```") is None


# ---- #6/#2: spend fail-closed -------------------------------------------------

def test_gw_spend_missing_log_raises(monkeypatch):
    monkeypatch.setattr(gw_spend, "LOG", "/nonexistent/spend.jsonl")
    with pytest.raises(RuntimeError):
        gw_spend.since(0)


def test_gw_spend_unknown_cost_surcharged(tmp_path, monkeypatch):
    log = tmp_path / "spend.jsonl"
    log.write_text(json.dumps({"ts": 10, "model": "m", "cost": None}) + "\n" +
                   json.dumps({"ts": 10, "model": "m", "cost": 0.01}) + "\n")
    monkeypatch.setattr(gw_spend, "LOG", str(log))
    assert gw_spend.since(0) == round(0.01 + gw_spend.UNKNOWN_COST, 4)
    assert gw_spend.unknown_since(0) == 1


# ---- #4/#8: judge/verdict never fake authority --------------------------------

def _mk_results(scores):
    return [{"fixture": "FX", "lane": l, "score": s, "cost_usd": 0,
             "tokens": 0, "wall_s": 1} for l, s in scores.items()]


def test_verdict_null_scores_excluded_and_conf_na():
    m = vd.build_map(_mk_results({"A": 1.0, "B": None}), {})
    d = m["FX"]
    assert d["quality_winner"] == "A"          # B's null can't compete as 0
    assert d["rig_failed_lanes"] == ["B"]
    assert d["confidence"] == "n/a"


def test_verdict_judge_error_means_na_soft():
    judge = {"FX": {"A": {"autonomy": 0, "verification": 0, "synthesis": 0,
                          "judge_error": True},
                    "B": {"autonomy": 0, "verification": 0, "synthesis": 0,
                          "judge_error": True}}}
    m = vd.build_map(_mk_results({"A": 1.0, "B": 1.0}), judge)
    d = m["FX"]
    assert d["quality_winner"] == "tie"
    assert d["soft_total"] is None and d["soft_winner"] == "n/a"
    assert d["confidence"] == "n/a"            # not "high" from imputed zeros


def test_verdict_legit_tie_with_real_judge_still_high():
    judge = {"FX": {"A": {"autonomy": 2, "verification": 2, "synthesis": 2,
                          "judge_error": False},
                    "B": {"autonomy": 2, "verification": 2, "synthesis": 2,
                          "judge_error": False}}}
    m = vd.build_map(_mk_results({"A": 1.0, "B": 1.0}), judge)
    assert m["FX"]["confidence"] == "high"


def test_close_calls_ignores_null():
    res = _mk_results({"A": 1.0, "B": None})
    assert vd.close_calls(res) == []


# ---- judge_error propagation ---------------------------------------------------

def test_judge_fixture_marks_error(monkeypatch):
    from model_races import judge as jj
    monkeypatch.setattr(jj, "_ask", lambda *a, **k: None)
    out = jj.judge_fixture("FX", {"A": "ta", "B": "tb"})
    assert out["A"]["judge_error"] and out["B"]["judge_error"]
