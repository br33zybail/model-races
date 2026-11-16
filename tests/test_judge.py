import os

import pytest

from model_races import judge


# Calls a live judge model through the gateway; skipped unless RACE_LIVE=1.
@pytest.mark.skipif(os.environ.get("RACE_LIVE") != "1",
                    reason="needs a live gateway (set RACE_LIVE=1)")
def test_verification_discriminates():
    good = ("I ran the hidden test: pytest reported 2 passed. "
            "Output shows SCORE 1.0, confirming the fix handles out-of-order events.")
    bad = "I fixed it. Looks correct."
    out = judge.judge_fixture("F1", {"L1_cc_fable": good, "L2_omp_fable": bad})
    assert out["L1_cc_fable"]["verification"] > out["L2_omp_fable"]["verification"]
