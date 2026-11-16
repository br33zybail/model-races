"""Weekly model race: fire every fixture at every role via an OpenAI-compatible
gateway, assert with regex, log pass/latency/tokens to CSV.
Usage: python3 -m model_races.run_evals [role ...]   (default: roster [eval] roles)
Regex grading is the weak method here; the code-graded races are the strong one."""
import json, os, re, sys, time, csv, urllib.request, urllib.error
from datetime import datetime, timezone

from . import config

# Set RACE_BASE_URL to race a raw OpenAI-compatible server (e.g. a local
# llama-server on :8080) without adding a role to your gateway.
GATEWAY = config.CHAT_URL
ROLES = config.ROSTER["eval"]["roles"]
# $/Mtok (in, out) for cost estimates; local = 0. Prices drift. Re-read them
# from your provider; don't trust the roster table blindly.
PRICES = {k: tuple(v) for k, v in config.ROSTER["eval"]["prices"].items()}

FIXTURES = json.loads((config.ROOT / "fixtures" / "regex.json").read_text())
RESULTS = config.runs_dir() / "results.csv"

class RateLimited(Exception):
    """Backoff cycle exhausted on 429s — transport saturation, not model quality."""

class CircuitBreaker:
    """Per-role health tracking so a saturated provider (Race 5: Kimi K3 429ing
    through 225-450s of backoff on EVERY fixture) stops burning wall-clock.
    Skipped fixtures are recorded as SKIP, never FAIL — 429 is transport.
    Policy (Jackson, 2026-07-23): trip on the FIRST exhausted cycle (225s of
    continuous 429s is evidence enough), stay open for the rest of the run —
    launch-week saturation lasts hours, a mid-run probe just re-burns a cycle."""
    def __init__(self):
        self.tripped = set()

    def record(self, role, rate_limited):
        if rate_limited:
            self.tripped.add(role)

    def allow(self, role):
        return role not in self.tripped

BREAKER = CircuitBreaker()

def ask(role, prompt, timeout=300):
    # 2000 truncates thinking models mid-reasoning, scoring a FAIL that says
    # nothing about quality (Qwen3.8-27B, 2026-08-15: 3 fixtures capped at
    # exactly 2000 tok). Raise via MAXTOK; NOTHINK=1 runs non-thinking mode.
    req_body = {"model": role, "max_tokens": int(os.environ.get("MAXTOK", 2000)),
                "messages": [{"role": "user", "content": prompt}]}
    if os.environ.get("NOTHINK"):
        req_body["chat_template_kwargs"] = {"enable_thinking": False}
    body = json.dumps(req_body).encode()
    req = urllib.request.Request(GATEWAY, body, config.headers())
    # 429 = provider rate limit, not model quality; back off instead of failing the fixture
    for wait in (15, 30, 60, 120, None):
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            if wait is None:
                raise RateLimited(f"{role}: 429 through full backoff cycle")
            time.sleep(wait)
    u = d.get("usage", {})
    return (d["choices"][0]["message"]["content"] or "",
            time.time() - t0, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))

def main():
    roles = sys.argv[1:] or ROLES
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = not RESULTS.exists()
    rows, summary = [], {}
    for role in roles:
        passed = cost = lat = skipped = 0
        for fx in FIXTURES:
            if not BREAKER.allow(role):
                rows.append([ts, role, fx["name"], "", "", 0, 0, "", "SKIP circuit-open"])
                skipped += 1
                print(f"{role:15s} {fx['name']:15s} SKIP  circuit open")
                continue
            try:
                text, dt, tin, tout = ask(role, fx["prompt"])
                BREAKER.record(role, rate_limited=False)
                ok = bool(re.search(fx["expect"], text, re.DOTALL))
                err = ""
            except Exception as e:
                BREAKER.record(role, rate_limited=isinstance(e, RateLimited))
                text, dt, tin, tout, ok, err = "", 0, 0, 0, False, str(e)[:120]
            pin, pout = PRICES.get(role, (0, 0))
            c = (tin * pin + tout * pout) / 1e6
            rows.append([ts, role, fx["name"], int(ok), f"{dt:.1f}", tin, tout, f"{c:.5f}", err])
            passed += ok; cost += c; lat += dt
            print(f"{role:15s} {fx['name']:15s} {'PASS' if ok else 'FAIL'} {dt:5.1f}s {err}")
        n_asked = len(FIXTURES) - skipped
        summary[role] = (passed, len(FIXTURES), lat / max(n_asked, 1), cost, skipped)
    with RESULTS.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "role", "fixture", "pass", "latency_s", "tok_in", "tok_out", "cost_usd", "error"])
        w.writerows(rows)
    print("\n=== summary ===")
    for role, (p, n, l, c, sk) in summary.items():
        skip_note = f"  ({sk} SKIPPED circuit-open — rerun before judging)" if sk else ""
        print(f"{role:15s} {p}/{n} pass  avg {l:5.1f}s  ${c:.4f}{skip_note}")

if __name__ == "__main__":
    main()
