# 07-relay-bad-deploy: first attempt (unedited)

- model: `gpt-6-luna`
- asked: 2026-11-06T18:02:56.163168+00:00
- prompt: `tasks/07-relay-bad-deploy/prompt.txt` (sha256 `cf29d4d146a0f07e…`)

---

**Root cause:** The 14:01:45 `notify-relay` v1.9.0 deployment introduced a broken Chatline client call: `TypeError: chatline.send is not a function`. `session-api` requires `notify-relay` for every request, and retries once, so the relay failure propagates as session-api 502s and then edge-gw 502s. The relay’s roughly doubled request rate is consistent with those retries; its 369 req/s is below Chatline’s 1,000 req/s quota.

**Do now:** Roll back `notify-relay` from v1.9.0 to the immediately preceding known-good release, at **100%**. The telemetry does not give that release’s version number, so don’t invent one. Then confirm `notify-relay` errors fall and `session-api`/`edge-gw` errors recover. Do not scale services or change the Chatline quota based on these metrics.

```json
{"root_cause_service":"notify-relay","mechanism":"The v1.9.0 deployment introduced a Chatline client API mismatch: chatline.send is not a function. notify-relay failures propagate through session-api, where notify-relay is required and retried once, and then appear as edge-gw 502s.","actions":["Roll back notify-relay from v1.9.0 at 100% to the immediately preceding known-good release; its version number is not present in the telemetry, so identify it from deployment history rather than guessing.","Verify notify-relay errors fall and session-api and edge-gw error rates recover."]}
```
