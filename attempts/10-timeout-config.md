# 10-timeout-config: first attempt (unedited)

- model: `gpt-6-luna`
- asked: 2026-11-06T18:06:14.496664+00:00
- prompt: `tasks/10-timeout-config/prompt.txt` (sha256 `698f21f19ed7ae19…`)

---

**Root cause:** `player-core`’s 14:01:50 config change (PR #5120) cut the `media-cache` client timeout from **1200 ms to 150 ms**. Cache requests then timed out while waiting in the overloaded cache queue; `player-core` retried each request **3 times**, amplifying cache traffic and pushing it to **100% utilization**. The resulting failures propagated through `session-api` and `edge-gw`. The origins and unrelated preview path do not show evidence of causing this incident.

**Do now, in this order:**
1. Set `player-core → media-cache` retries from **3 to 0** to stop retry amplification.
2. Roll back the `player-core → media-cache` timeout from **150 ms to 1200 ms** (the pre-incident value).

Do not change the origins or clip-preview path based on these metrics.

```json
{"root_cause_service":"player-core","mechanism":"The 150 ms media-cache client timeout introduced by PR #5120 caused queued cache requests to time out; player-core's 3 retries amplified traffic until media-cache saturated, and failures propagated through session-api to edge-gw.","actions":["Set player-core -> media-cache retries from 3 to 0.","Set player-core -> media-cache timeout from 150 ms back to 1200 ms."]}
```
