"""Sum gateway USD spend recorded after a given epoch.

Fail-closed contract (grok review 2026-07-12): a missing spend log is NOT
"zero spend" — it means the budget gate is blind, so we raise instead of
returning 0.0. Records with unknown cost (cost: null from the logger) are
counted at a conservative surcharge so budget gates overestimate rather
than underestimate.
"""
import json
import os
import sys

# One JSON object per gateway call: {"ts": <epoch>, "model": "...", "cost": <usd or null>}.
# Your gateway needs a success callback that appends these. Set RACE_SPEND_LOG.
LOG = os.environ.get("RACE_SPEND_LOG", "runs/spend.jsonl")
# Conservative per-call estimate for unknown-cost records; observed p95 of
# real per-call costs in the 2026-07-12 sweeps was well under this.
UNKNOWN_COST = float(os.environ.get("GW_UNKNOWN_COST", "0.05"))


def _scan(ts_epoch: float):
    if not os.path.exists(LOG):
        raise RuntimeError(
            f"spend log missing at {LOG} — budget gate would be blind; "
            f"fix the gateway spend_logger (or touch the file if this is a "
            f"fresh deploy) instead of assuming $0 spent")
    total, unknown = 0.0, 0
    with open(LOG) as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("ts", 0) >= ts_epoch:
                c = r.get("cost")
                if c is None:
                    unknown += 1
                    total += UNKNOWN_COST
                else:
                    total += float(c)
    return round(total, 4), unknown


def since(ts_epoch: float) -> float:
    """Spend after ts_epoch; unknown-cost records counted at UNKNOWN_COST each."""
    total, _ = _scan(ts_epoch)
    return total


def unknown_since(ts_epoch: float) -> int:
    """How many records after ts_epoch had no logged cost."""
    _, unknown = _scan(ts_epoch)
    return unknown


if __name__ == "__main__":
    ts = float(sys.argv[1]) if len(sys.argv) > 1 else 0
    t, u = _scan(ts)
    print(t, f"({u} unknown-cost records surcharged at ${UNKNOWN_COST})" if u else "")
