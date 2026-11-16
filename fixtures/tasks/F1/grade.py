import os
import re
import sys
import tempfile
import textwrap

# The harness runs graders with `python -I`, which leaves the repo off sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from model_races.sandbox import run_code  # noqa: E402

# Integrity contract (grok review 2026-07-12): submission code runs with its
# stdout suppressed, and the score travels via a private file the inner runner
# always writes on a legitimate finish. A submission that prints "SCORE 1.0"
# or os._exit()s mid-import produces a MISSING score file -> GRADE-ERROR and
# exit 1, which the harness treats as null, never as a number.

TEST = textwrap.dedent('''
    import importlib.util, os, io, contextlib
    def load(ws):
        spec = importlib.util.spec_from_file_location("wh", os.path.join(ws, "webhook.py"))
        m = importlib.util.module_from_spec(spec)
        with contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(m)
        return m
    def test_out_of_order(ws):
        m = load(ws)
        # deleted(ts=2) arrives before created(ts=1): the sub must stay deleted.
        # Both removal and tombstoning are valid fixes — what matters is the
        # sub must NOT be active (learned from a run where a correct tombstone
        # fix was scored 0).
        with contextlib.redirect_stdout(io.StringIO()):
            out = m.process([{"type":"deleted","sub":"s1","ts":2},
                             {"type":"created","sub":"s1","ts":1}], {})
        active = "s1" in out and out["s1"].get("status") == "active"
        assert not active, "late created resurrected a cancelled sub"
    def test_normal(ws):
        m = load(ws)
        with contextlib.redirect_stdout(io.StringIO()):
            out = m.process([{"type":"created","sub":"s2","ts":1}], {})
        assert out.get("s2",{}).get("status")=="active"
''')


def main():
    ws = os.path.abspath(sys.argv[1])
    with tempfile.TemporaryDirectory() as td:
        scorefile = os.path.join(td, "score.out")
        script = (TEST + f'\nok=0\nfor fn in [test_out_of_order,test_normal]:\n'
                         f'    try: fn({ws!r}); ok+=1\n'
                         f'    except Exception: pass\n'
                         f'open({scorefile!r},"w").write(str(ok/2))\n')
        # Sandboxed: own temp cwd, clean env, rlimits, timeout (sandbox.py).
        p = run_code({"t.py": script}, "t.py", timeout=30)
        if p.timed_out:
            print("GRADE-ERROR: inner runner timeout")
            sys.exit(1)
        if not os.path.exists(scorefile):
            print(f"GRADE-ERROR: no score file (rc={p.returncode}; submission "
                  f"crashed the runner or exited early)")
            sys.exit(1)
        val = open(scorefile).read().strip()
        if not re.fullmatch(r"[01](\.\d+)?", val):
            print(f"GRADE-ERROR: malformed score {val!r}")
            sys.exit(1)
    print("SCORE", val)


if __name__ == "__main__":
    main()
