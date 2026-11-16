import os
import re
import sys
import tempfile
import textwrap

# The harness runs graders with `python -I`, which leaves the repo off sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from model_races.sandbox import run_code  # noqa: E402

# Integrity contract (grok review 2026-07-12): the old version graded on a
# SUBSTRING match of "SCORE 1.0" in inner stdout, so a submission that merely
# printed that string at import time passed without an allow() callable.
# Now: submission stdout suppressed, contract checked with asserts, score via
# private file. Legit contract failure -> 0.0; runner killed early -> missing
# file -> GRADE-ERROR exit 1 (null upstream, never a number).


def main():
    ws = os.path.abspath(sys.argv[1])
    with tempfile.TemporaryDirectory() as td:
        scorefile = os.path.join(td, "score.out")
        probe = textwrap.dedent(f'''
            import importlib.util, os, io, contextlib
            score = 0.0
            try:
                p = os.path.join({ws!r}, "ratelimit.py")
                assert os.path.exists(p), "no ratelimit.py"
                spec = importlib.util.spec_from_file_location("rl", p)
                m = importlib.util.module_from_spec(spec)
                with contextlib.redirect_stdout(io.StringIO()):
                    spec.loader.exec_module(m)
                    assert callable(getattr(m, "allow", None)), "no allow(key) callable"
                    assert isinstance(m.allow("k"), bool)
                score = 1.0
            except Exception:
                score = 0.0
            open({scorefile!r}, "w").write(str(score))
        ''')
        # Sandboxed: own temp cwd, clean env, rlimits, timeout (sandbox.py).
        p = run_code({"p.py": probe}, "p.py", timeout=20)
        if p.timed_out:
            print("GRADE-ERROR: probe timeout")
            sys.exit(1)
        if not os.path.exists(scorefile):
            print(f"GRADE-ERROR: no score file (rc={p.returncode}; submission "
                  f"killed the probe)")
            sys.exit(1)
        val = open(scorefile).read().strip()
        if not re.fullmatch(r"[01](\.\d+)?", val):
            print(f"GRADE-ERROR: malformed score {val!r}")
            sys.exit(1)
    print("SCORE", val)


if __name__ == "__main__":
    main()
