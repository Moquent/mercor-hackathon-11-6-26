# 04-docstore-poller: first attempt (unedited)

- model: `gpt-6-luna`
- asked: 2026-11-06T18:02:56.163094+00:00
- prompt: `tasks/04-docstore-poller/prompt.txt` (sha256 `9eb72dee94cbc8f5…`)

---

The initiating fault is the **sub-poller v1.8.0 change**: it changed subscription sync from every 60s to every 5s and runs a full scan per worker. Docstore then jumps from about 300 to about 4,000 requests/s, reaches 100% utilization, and shows slow `subscriptions.find({})` collection scans. Session API’s docstore calls time out; edge-gw’s 504s are downstream symptoms. The telemetry does not support changing the other dependencies.

**Do this now:**
1. Pause the sub-poller to stop the scan load: set its worker count to **0**.
2. Roll it back to the revision running before **14:01:40**. Restore the **60s** sync interval and the prior scan behavior; do not resume the v1.8.0 full-scan-per-worker behavior.
3. Watch docstore load and session-api errors recover before resuming the sub-poller under the rolled-back behavior.

```json
{"root_cause_service":"sub-poller","mechanism":"v1.8.0 increased subscription sync frequency from 60s to 5s and performed a full subscriptions scan per worker, saturating docstore and causing session-api docstore timeouts and edge-gw 504s.","actions":["Pause the sub-poller now by setting worker count to 0.","Roll back sub-poller to the revision running before 14:01:40; restore the 60s sync interval and prior scan behavior, with no full scan per worker.","Verify docstore load and session-api errors recover before resuming the sub-poller under the rolled-back behavior."]}
```
