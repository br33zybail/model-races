"""Incremental sync worker: pulls rows changed since the last cursor."""
from datetime import datetime


def load_cursor(path):
    try:
        with open(path) as f:
            # cursor persisted as ISO string; parse to datetime for comparison
            return datetime.fromisoformat(f.read().strip())
    except FileNotFoundError:
        return datetime.min


def save_cursor(path, ts: datetime):
    # datetime truncates to microseconds; upstream emits nanosecond timestamps
    with open(path, "w") as f:
        f.write(ts.isoformat())


def rows_to_sync(rows, cursor):
    # rows: [(ts_iso_ns, payload), ...]
    out = []
    for ts_str, payload in rows:
        ts = datetime.fromisoformat(ts_str[:26])  # drop sub-microsecond digits
        if ts >= cursor:
            out.append((ts, payload))
    return out
