# 13-cache-pool: first attempt (unedited)

- model: `gpt-6-luna`
- asked: 2026-11-06T18:08:06.838028+00:00
- prompt: `tasks/13-cache-pool/prompt.txt` (sha256 `b5d35d0a2534d355…`)

---

**Root cause: `media-cache`.** At 14:01:55, PR #5203 reduced its origin connection-pool limit from **512 to 96**. The pool then saturated at 96/96; queue wait rose to 2.8 seconds, exceeding the downstream timeouts. `player-core` timed out and retried cache requests, amplifying load; its failures then propagated through `session-api` to `edge-gw`. The origin metrics do not show errors, and the other listed services do not show a comparable initiating change.

**Do now:** revert the `media-cache` origin connection-pool setting to **512** (undo the 96-connection change in PR #5203). The telemetry supports this as the direct corrective action; it does not establish that another configuration change is required.

```json
{"root_cause_service":"media-cache","mechanism":"The origin connection-pool limit was reduced from 512 to 96, saturating the pool and pushing queue waits beyond downstream timeouts. player-core retries amplified cache load, and failures propagated to session-api and edge-gw.","actions":["Revert media-cache origin pool max_connections from 96 to 512 (undo PR #5203)."]}
```
