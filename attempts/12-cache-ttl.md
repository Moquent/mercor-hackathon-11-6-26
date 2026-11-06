# 12-cache-ttl: first attempt (unedited)

- model: `gpt-6-luna`
- asked: 2026-11-06T18:08:06.837722+00:00
- prompt: `tasks/12-cache-ttl/prompt.txt` (sha256 `62102e8027706a1a…`)

---

**Root cause: `media-cache`.** The cache TTL was reduced from **3600s to 60s** at 14:01:48. By 14:02:10, the hit ratio had fallen from **94% to 31%** and origin fetches per request had risen from **0.06 to 0.69**. `media-cache` then saturated at **100% utilisation**, with **6.2–6.8s p99** and a **1.9s queue wait**. `player-core` timed out waiting for it; retries amplified the load, and failures propagated through `session-api` to `edge-gw`. The origin and unrelated services do not show the initiating failure.

**Do now:**
1. Roll back the `media-cache` segment-cache TTL from **60s to 3600s**.
2. Temporarily set `player-core → media-cache` retries from **3 to 0**. Its four attempts are timing out and adding load to the saturated cache.
3. Temporarily set `session-api → player-core` retries from **1 to 0** to avoid repeating failing player calls while the cache recovers.

```json
{"root_cause_service":"media-cache","mechanism":"Reducing segment-cache TTL from 3600s to 60s drove cache hit ratio from 94% to 31% and origin fetches per request from 0.06 to 0.69, saturating media-cache. player-core retries and session-api retries amplified the resulting timeouts, which propagated as errors to edge-gw.","actions":["Roll back media-cache segment cache TTL from 60s to 3600s.","Set player-core -> media-cache retries from 3 to 0 temporarily.","Set session-api -> player-core retries from 1 to 0 temporarily."]}
```
