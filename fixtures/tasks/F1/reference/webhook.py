def process(events, store):
    seen = {}
    for e in events:
        sub, ts = e["sub"], e["ts"]
        if ts < seen.get(sub, -1):
            continue
        seen[sub] = ts
        if e["type"] == "created":
            store[sub] = {"status": "active", "ts": ts}
        elif e["type"] == "deleted":
            store.pop(sub, None)
    return store
