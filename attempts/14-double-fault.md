# 14-double-fault: first attempt (unedited)

- model: `gpt-6-luna`
- asked: 2026-11-06T18:23:01.065762+00:00
- prompt: `tasks/14-double-fault/prompt.txt` (sha256 `1ebf2d70c5c5ed1d…`)

---

There are **two concurrent causes**:

- **notify-relay overload:** At 14:02:15, its traffic jumped from about 194 req/s to 1,619 req/s and it saturated at 100% utilization. The error log shows Chatline rejecting excess traffic above its **1,000 req/s** global quota. This coincides with the session-api deployment starting the bulk message-cleanup job. Session-api retries dependency calls once, adding further load.
- **meta-index authentication failure:** Production replica `mi-1` is using the staging-rotated key `lw-meta-key-2`; its 401s explain the persistent 30% meta-index error rate.

**Do now:**
1. Stop the session-api `msg-cleanup` bulk DELETE job. Don’t try to solve the Chatline quota breach by scaling notify-relay: the external quota is global.
2. Drain `mi-1` from the production meta-index pool until its credential is corrected, then install the valid **production** key and return it to service. The telemetry does not provide the production key’s value, so do not substitute a guessed value.

```json
{"root_cause_service":"session-api cleanup job and meta-index mi-1","mechanism":"The cleanup job drove notify-relay above Chatline's 1000 req/s global quota, causing 429s and retries; separately, meta-index replica mi-1 used staging key lw-meta-key-2 and returned 401s.","actions":["Stop the session-api msg-cleanup bulk DELETE job now.","Drain meta-index replica mi-1 from production until corrected; replace lw-meta-key-2 with the valid production key, then return mi-1 to service."]}
```
