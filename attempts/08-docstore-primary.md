# 08-docstore-primary: first attempt (unedited)

- model: `gpt-6-luna`
- asked: 2026-11-06T18:02:56.163219+00:00
- prompt: `tasks/08-docstore-primary/prompt.txt` (sha256 `5ec0b2ca087c67ac…`)

---

**Root cause:** `docstore`. At 14:01:50 its primary, `ds-0`, became unreachable after a kernel panic; automatic failover is not configured. `docstore` then returned 100% errors, and because `session-api` requires docstore, `session-api` and `edge-gw` also returned 100% errors. The other required session dependencies remain healthy. Session-api’s one retry is amplifying docstore traffic.

**Do now, in order:**
1. Recover or reboot `ds-0` to restore the docstore primary. If it cannot be recovered, manually promote a **confirmed healthy** docstore replica. The telemetry names no replica or promotion target, so it cannot support an exact target.
2. As a temporary mitigation while docstore is unavailable, change `session-api -> docstore` retries from **1 to 0**. Restore retries to **1** after docstore is healthy.
3. Confirm `docstore`, `session-api`, and `edge-gw` errors return to **0%**. Do not change the healthy dependencies.

```json
{"root_cause_service":"docstore","mechanism":"The ds-0 primary became unreachable after a kernel panic at 14:01:50; with no automatic failover, docstore returned connection-refused errors. Since docstore is required by session-api, those failures propagated to session-api and edge-gw; session-api's one retry amplified docstore traffic.","actions":["Recover or reboot docstore primary ds-0; if unrecoverable, manually promote a confirmed healthy replica (no replica target is identified in the telemetry).","Temporarily set session-api -> docstore retries from 1 to 0 while docstore is unavailable; restore to 1 after recovery.","Verify docstore, session-api, and edge-gw error rates return to 0%."]}
```
