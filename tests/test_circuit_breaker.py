"""Circuit-breaker contract tests (Race 5 Kimi K3 lesson, added 2026-07-23).

Invariants: a full exhausted 429 backoff cycle raises RateLimited (not a bare
HTTPError), trips the role's circuit immediately, and the circuit stays open
for the rest of the run. Quality outcomes never touch transport state.
Policy per Jackson 2026-07-23: trip on first exhausted cycle, no half-open probe.

Runs with pytest, or standalone with `python3 -m tests.test_circuit_breaker`.
"""
import io
import urllib.error

from model_races import run_evals as re_mod


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b""))


def test_trips_on_first_exhausted_cycle():
    b = re_mod.CircuitBreaker()
    assert b.allow("kimi")
    b.record("kimi", rate_limited=True)
    assert not b.allow("kimi")


def test_stays_open_despite_later_success():
    b = re_mod.CircuitBreaker()
    b.record("kimi", rate_limited=True)
    b.record("kimi", rate_limited=False)  # e.g. a stray success elsewhere
    assert not b.allow("kimi"), "no half-open reset: circuit holds for the run"


def test_quality_failures_never_trip():
    b = re_mod.CircuitBreaker()
    for _ in range(10):  # ten FAILs (or timeouts, or 500s) are not saturation
        b.record("flaky", rate_limited=False)
    assert b.allow("flaky")


def test_roles_are_independent():
    b = re_mod.CircuitBreaker()
    b.record("kimi", rate_limited=True)
    assert b.allow("laguna")


def _with_urlopen(exc_factory, fn):
    """Run fn with urlopen always raising and sleep a no-op; restore after."""
    calls = []
    def fake_urlopen(*a, **k):
        calls.append(1)
        raise exc_factory()
    orig_open, orig_sleep = re_mod.urllib.request.urlopen, re_mod.time.sleep
    re_mod.urllib.request.urlopen, re_mod.time.sleep = fake_urlopen, lambda s: None
    try:
        fn()
    finally:
        re_mod.urllib.request.urlopen, re_mod.time.sleep = orig_open, orig_sleep
    return calls


def test_exhausted_backoff_raises_ratelimited():
    def go():
        try:
            re_mod.ask("kimi", "hi")
        except re_mod.RateLimited:
            return
        raise AssertionError("expected RateLimited after exhausted backoff")
    _with_urlopen(lambda: http_error(429), go)


def test_non_429_errors_raise_immediately():
    def go():
        try:
            re_mod.ask("kimi", "hi")
        except urllib.error.HTTPError:
            return
        raise AssertionError("expected HTTPError to propagate")
    calls = _with_urlopen(lambda: http_error(500), go)
    assert len(calls) == 1, "500s must not consume the 429 backoff ladder"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    raise SystemExit(1 if fails else 0)
