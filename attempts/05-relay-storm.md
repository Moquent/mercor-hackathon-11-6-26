# 05-relay-storm: first attempt (unedited)

- model: `gpt-6-luna`
- asked: 2026-11-06T18:02:56.163009+00:00
- prompt: `tasks/05-relay-storm/prompt.txt` (sha256 `2453d65abb04b667…`)

---

**Root cause:** The session-api v4.12.0 deployment started a bulk DELETE cleanup job at 14:01:40. Its notification traffic pushed `notify-relay` to about 1,800 requests/s—above Chatline’s 1,000 requests/s global quota—causing 429s. Because notifications are required and retried once, those failures propagated to session-api and then edge-gw.

**Do now:** Stop the post-deploy `msg-cleanup` bulk DELETE job. Do not scale `notify-relay`: the limiting factor is Chatline’s quota, not merely relay capacity. The clip-preview errors are a separate, pre-existing issue and are not on the listener request path.

```json
{"root_cause_service":"notify-relay","mechanism":"The session-api v4.12.0 post-deploy bulk DELETE cleanup job flooded Chatline above its 1000 req/s global quota, causing 429s that propagated through the required notify-relay dependency to session-api and edge-gw.","actions":["Stop the post-deploy msg-cleanup bulk DELETE job now."]}
```
