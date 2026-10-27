# Ring checks

`ring_slo.js` runs during each rollout ring against the **live** service URL (not the canary tag URL),
at 20 req/s for `SOAK_SECONDS` (default 120). It fails (k6 exit 99) when:

- more than 1% of requests fail, or p95 latency is 400 ms or more;
- any `/api/v1/balance` response shows another user's `user_id`, or lacks `Cache-Control: private, no-store`.

**Honesty note:** Cloud Run has no shared cache in this path. The isolation check guards the app's
cache contract (`private, no-store`, `Vary: Authorization`), which is what stops a CDN from caching a
per-user response. It does not prove that any particular CDN is safe.

Claim probes (trusted partner 200, distrusted partner 502) run only against the canary **tag URL**,
because during a ring most live traffic still reaches the old revision.

Local run: `k6 run -e BASE_URL=http://127.0.0.1:8080 -e SOAK_SECONDS=10 loadtest/ring_slo.js`
