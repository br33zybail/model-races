We need a token-bucket rate limit on the /convert endpoint of a small web
service (anonymous users, keyed by client IP; default 10 requests/minute,
burst 20).

Produce, in the working directory:
1. plan.md — a short implementation plan: decomposition into steps, what each
   step touches, how you'd verify each step, and any trade-offs you weighed
   (memory vs redis, clock source, cleanup of stale buckets).
2. ratelimit.py — a minimal working stub exposing allow(key) -> bool that
   implements the token bucket in memory (stdlib only).

The plan is the main deliverable; the stub proves the interface is real.
