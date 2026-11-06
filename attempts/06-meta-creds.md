# 06-meta-creds: first attempt (unedited)

- model: `gpt-6-luna`
- asked: 2026-11-06T18:02:56.162821+00:00
- prompt: `tasks/06-meta-creds/prompt.txt` (sha256 `5d45455e7e684d26…`)

---

**Root cause:** `meta-index` authentication failure. At 14:00:40, staging rotated the shared production API key to `lw-meta-key-2`; `meta-index` then returned `401 invalid_client` for that key. `session-api` requires `meta-index` on every request, so its failures propagated to `edge-gw`. The failures began before the `session-api` deploy, so the deploy is not the cause.

**Do now:** Roll back the shared-key rotation: restore the previous production-valid secret from the secret manager and ensure the production `session-api` client and `meta-index` use the same valid credential. The telemetry does not provide the previous secret’s literal value, so retrieve it from the secret manager rather than guessing. Do not change traffic, scale services, or roll back the `session-api` deploy based on these metrics.

```json
{"root_cause_service":"meta-index","mechanism":"The shared API key was rotated to lw-meta-key-2, which meta-index rejects with 401 invalid_client; required meta-index lookups then fail in session-api and propagate as errors at edge-gw.","actions":["Roll back the shared-key rotation by restoring the previous production-valid secret from the secret manager, and ensure production session-api and meta-index use the same valid credential; the previous secret value is not present in the telemetry."]}
```
