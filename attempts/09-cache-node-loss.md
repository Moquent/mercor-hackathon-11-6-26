# 09-cache-node-loss: first attempt (unedited)

- model: `gpt-6-luna`
- asked: 2026-11-06T18:06:14.496420+00:00
- prompt: `tasks/09-cache-node-loss/prompt.txt` (sha256 `b459d0689b0cb2d2…`)

---

**Root cause:** `media-cache` lost two of its three healthy pods after node `lw-n7` went NotReady from disk pressure. Its remaining pod is saturated, and its 1.7 s queue wait exceeds `player-core`’s 1.2 s timeout. Retries amplify the load: `session-api` retries `player-core` once, and `player-core` retries the cache three times. The resulting timeouts cascade to `session-api` and `edge-gw`. The origin services are not reporting errors.

**Do now:**
1. Temporarily set `player-core → media-cache` retries from **3 to 0**.
2. Temporarily set `session-api → player-core` retries from **1 to 0**.
3. Resolve disk pressure on `lw-n7` and provide enough cache-pool CPU capacity to schedule the two pending pods; restore `media-cache` to **3/3 healthy pods**. The telemetry does not give the pool’s current max-node value or per-pod CPU requirements, so it cannot support a safe numeric node-count change.

```json
{"root_cause_service":"media-cache","mechanism":"Disk pressure made node lw-n7 NotReady and evicted two of three cache pods; the remaining pod saturated, causing queue wait above player-core's timeout. Retries at player-core and session-api amplified cache traffic and cascaded errors to session-api and edge-gw.","actions":["Set player-core -> media-cache retries from 3 to 0 temporarily.","Set session-api -> player-core retries from 1 to 0 temporarily.","Resolve disk pressure on node lw-n7 and add cache-pool CPU capacity until both pending media-cache pods can schedule; restore media-cache to 3/3 healthy pods. The telemetry does not provide enough information to specify a numeric node-count increase."]}
```
