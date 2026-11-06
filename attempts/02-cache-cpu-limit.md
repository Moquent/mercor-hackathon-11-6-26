# 02-cache-cpu-limit: first attempt (unedited)

- model: `gpt-6-luna`
- asked: 2026-11-06T18:02:56.162541+00:00
- prompt: `tasks/02-cache-cpu-limit/prompt.txt` (sha256 `901def583ff0a9e3…`)

---

**Root cause: `media-cache`.** Its CPU limit was reduced from **2000m to 800m** at 14:01:52. The resulting CPU throttling (91%) and 2.0s queue wait caused requests to exceed `player-core`’s 1200ms timeout. Retries amplified the cache load and traffic to `vela-origin`; failures then propagated through `player-core`, `session-api`, and `edge-gw`. The later `player-core` deploy is not supported by the telemetry as the initiating cause: the failure began before it rolled out.

**Do now:**
1. Restore `media-cache`’s `resources.limits.cpu` to **2000m** and roll out the change to all **3/3 pods**.

```json
{"root_cause_service":"media-cache","mechanism":"The CPU limit was reduced from 2000m to 800m, causing 91% CPU throttling and 2.0s queue wait. Cache requests exceeded player-core's 1200ms timeout, triggering retries and cascading failures.","actions":["Restore media-cache resources.limits.cpu to 2000m and roll out the change to all 3/3 pods."]}
```
