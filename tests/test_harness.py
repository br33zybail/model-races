import os

from model_races import race_harness as H


def test_workspaces_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DRYRUN", "1")
    r1 = H.run_lane("L1_cc_fable", "F1", str(tmp_path / "a"))
    r2 = H.run_lane("L2_omp_fable", "F1", str(tmp_path / "b"))
    assert os.path.exists(tmp_path / "a" / "webhook.py")  # seed copied in
    assert os.path.realpath(tmp_path / "a") != os.path.realpath(tmp_path / "b")
    assert set(r1) >= {"lane", "fixture", "score", "wall_s", "tokens", "cost_usd"}
    assert set(r2) >= {"lane", "fixture", "score", "wall_s", "tokens", "cost_usd"}
