The file webhook.py processes subscription webhook events, but has a bug: events
can arrive OUT OF ORDER (a 'deleted' before its 'created', by timestamp `ts`).
Find the root cause, fix webhook.py in place, and verify your fix. Keep the
process(events, store) signature. Do not add dependencies.
