# Process Stripe-style subscription webhooks. BUG: events can arrive out of order.
def process(events, store):
    for e in events:
        if e["type"] == "created":
            store[e["sub"]] = {"status": "active", "ts": e["ts"]}
        elif e["type"] == "deleted":
            store.pop(e["sub"], None)
    return store
