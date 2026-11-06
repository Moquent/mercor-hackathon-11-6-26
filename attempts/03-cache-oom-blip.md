# 03-cache-oom-blip: first attempt (unedited)

- model: `gpt-6-luna`
- asked: 2026-11-06T18:02:56.162928+00:00
- prompt: `tasks/03-cache-oom-blip/prompt.txt` (sha256 `bbbb8401795fb361…`)

---

**Root cause:** `media-cache` became the bottleneck after `mc-2` was OOM-killed at 14:02:00. Its latency rose, and calls from `player-core` began timing out. Retries then amplified the load: `session-api` retries `player-core` once, and `player-core` retries `media-cache` three times. Together, those policies can turn roughly 200 requests/s into roughly 1,600 cache attempts/s—the observed jump. The pod replacement restored `3/3` health, but the amplified traffic kept the cache saturated. The `player-core` deploy occurred after the incident began, so this telemetry does not support it as the initial cause.

**Do now:**
1. Temporarily set `session-api -> player-core` retries from **1 to 0**.
2. Temporarily set `player-core -> media-cache` retries from **3 to 0**.

That removes both retry multipliers; the metrics indicate cache traffic should fall toward its pre-incident level. Do not roll back the `player-core` deploy based on this evidence alone.

```json
{"root_cause_service":"media-cache","mechanism":"mc-2 OOM reduced cache capacity and caused timeouts; retries at session-api (1) and player-core (3) amplified traffic by up to 8x, keeping media-cache saturated after 3/3 pods recovered.","actions":["Set session-api -> player-core retries from 1 to 0 temporarily.","Set player-core -> media-cache retries from 3 to 0 temporarily."]}
```
