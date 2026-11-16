from model_races import verdict


def test_no_significant_difference_is_legal():
    results = [{"lane": "L1_cc_fable", "fixture": "F1", "score": 1.0,
                "cost_usd": 0.0, "wall_s": 30, "tokens": 9000},
               {"lane": "L2_omp_fable", "fixture": "F1", "score": 1.0,
                "cost_usd": 1.4, "wall_s": 40, "tokens": 0}]
    judge = {"F1": {"L1_cc_fable": {"autonomy": 3, "verification": 3, "synthesis": 3},
                    "L2_omp_fable": {"autonomy": 3, "verification": 3, "synthesis": 3}}}
    m = verdict.build_map(results, judge)
    assert m["F1"]["quality_winner"] == "tie"
    assert m["F1"]["confidence"] in ("high", "low")


def test_clear_winner_and_close_calls():
    results = [{"lane": "L1_cc_fable", "fixture": "F2", "score": 1.0,
                "cost_usd": 0.0, "wall_s": 30, "tokens": 9000},
               {"lane": "L3_omp_cheap_escalate", "fixture": "F2", "score": 0.6,
                "cost_usd": 0.0, "wall_s": 20, "tokens": 0}]
    m = verdict.build_map(results, {})
    assert m["F2"]["quality_winner"] == "L1_cc_fable"
    assert verdict.close_calls(results) == []
