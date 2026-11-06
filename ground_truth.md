# Ground truth

Each task is a seeded simulation, so the right answer is **computed**: every executable action in the catalog (singles, then
pairs) is replayed from the page time (14:05:00), and a set counts as a fix if the `edge-gw` error rate stays under
5% over 14:07:30–14:10:00. The rationale explains *why* in plain terms; the replay numbers are the proof.

## 01-vela-402 — Primary origin billing lapse -> fallback flood

**What happened:** vela-origin started returning HTTP 402 at 14:01:50 (billing lapse). Its traffic fell through to korra (which misses unlicensed catalog) and brio (quota 60 req/s -> 429s). brio's 429s are the loudest signal but are a symptom of the load shift. Restoring vela access ends the incident; raising brio limits does not, because brio and korra still miss part of the catalog.

**Minimal sufficient fixes (computed):** `restore_vela`

**Without action:** entry error rate 40% in the judged window.

**Tempting wrong answer:** ['Raise the brio-origin rate limit / ask brio for more quota'] → verifier: **REJECT** (entry error rate after replay 10%).

## 02-cache-cpu-limit — Persistent capacity cut + retry amplification (needs two actions)

**What happened:** The CPU limit cut dropped media-cache capacity below normal demand. Timeouts triggered 2x4 layered retries, so media-cache now receives ~8x the entry rate. Reverting the CPU limit alone restores 450 req/s, still far below the ~1600 req/s retry load, so the system stays saturated. It needs the revert AND retry amplification removed (or >=4x scale-out).

**Minimal sufficient fixes (computed):** `scale_media_cache_4x` + `rollback_media_cache`; `scale_media_cache_4x` + `player_retries_off`; `rollback_media_cache` + `player_retries_off`

**Without action:** entry error rate 100% in the judged window.

**Tempting wrong answer:** ['Revert media-cache CPU limit to 2000m'] → verifier: **PARTIAL** (entry error rate after replay 100%).

## 03-cache-oom-blip — Metastable retry storm after a recovered OOM

**What happened:** A 30 s OOM blip pushed media-cache into overload. Layered retries (x8) keep offered load above capacity after the pods recovered: a metastable failure. Restarting/scaling media-cache 2x does nothing; removing the player-core retries drains it.

**Minimal sufficient fixes (computed):** `scale_media_cache_4x`; `player_retries_off`

**Without action:** entry error rate 100% in the judged window.

**Tempting wrong answer:** ['Restart the media-cache pods'] → verifier: **PARTIAL** (entry error rate after replay 100%).

## 04-docstore-poller — Cron poll-interval change saturates the database

**What happened:** sub-poller v1.8.0 multiplied background load on docstore (~3600 extra req/s against 1500 capacity). Restarting docstore drops the queue for seconds before the poller refills it; 2x scale is not enough. Rolling back/pausing the poller fixes it.

**Minimal sufficient fixes (computed):** `rollback_sub_poller`

**Without action:** entry error rate 100% in the judged window.

**Tempting wrong answer:** ['Restart docstore'] → verifier: **PARTIAL** (entry error rate after replay 100%).

## 05-relay-storm — Cleanup job hits external rate limit; loud pre-existing noise elsewhere

**What happened:** The msg-cleanup job started by the v4.12.0 deploy sends ~1500 extra req/s through notify-relay; Chatline's 1000 req/s global quota rejects ~60% with 429, failing session-api requests. clipstream errors are pre-existing, off the request path. Rolling back session-api would itself trigger another cleanup run; pausing msg-cleanup fixes it.

**Minimal sufficient fixes (computed):** `stop_msg_cleanup`

**Without action:** entry error rate 19% in the judged window.

**Tempting wrong answer:** ['Restart clip-preview and clipstream-origin'] → verifier: **REJECT** (entry error rate after replay 19%).

## 06-meta-creds — Credential expiry with a red-herring deploy right after

**What happened:** meta-index began returning 401 at 14:01:40 because staging rotated a key shared with prod. The session-api deploy came 40 s after errors began and does not touch meta-index; rolling it back changes nothing. Restoring/rotating the credential fixes it.

**Minimal sufficient fixes (computed):** `rotate_meta_creds`

**Without action:** entry error rate 49% in the judged window.

**Tempting wrong answer:** ['Roll back the session-api v4.12.0 deploy'] → verifier: **REJECT** (entry error rate after replay 49%).

## 07-relay-bad-deploy — CONTROL: bad deploy that really is the cause

**What happened:** notify-relay v1.9.0 broke sends (TypeError) the moment it rolled out. Rolling it back fixes the incident.

**Minimal sufficient fixes (computed):** `rollback_notify_relay`

**Without action:** entry error rate 72% in the judged window.

**Tempting wrong answer:** ['Restart session-api pods'] → verifier: **REJECT** (entry error rate after replay 72%).

## 08-docstore-primary — CONTROL: database primary crash

**What happened:** The docstore primary crashed. Restarting / failing over docstore fixes it.

**Minimal sufficient fixes (computed):** `restart_docstore`

**Without action:** entry error rate 100% in the judged window.

**Tempting wrong answer:** ['Set session-api retries to 0'] → verifier: **REJECT** (entry error rate after replay 100%).

## 09-cache-node-loss — Permanent node loss: reschedule alone and retries-off alone both fail

**What happened:** A node failure left media-cache at 1/3 pods (150 req/s, below the 200 req/s base load). Timeouts triggered x8 layered retries. Rescheduling the pods (restart/replace) restores 450 req/s but the retry load (~1600 req/s) keeps it saturated; disabling retries alone leaves 150 req/s capacity under 200+ req/s demand. It needs both (or 4x scale + one of them).

**Minimal sufficient fixes (computed):** `restart_media_cache` + `scale_media_cache_4x`; `restart_media_cache` + `player_retries_off`; `scale_media_cache_4x` + `player_retries_off`

**Without action:** entry error rate 100% in the judged window.

**Tempting wrong answer:** ['Set player-core -> media-cache retries to 0'] → verifier: **PARTIAL** (entry error rate after replay 100%).

## 10-timeout-config — Timeout set below normal latency; revert alone leaves a retry storm

**What happened:** PR #5120 set player-core's media-cache timeout (150 ms) below media-cache's normal latency (~160 ms), so most calls time out and are retried x4 (x8 with session-api). media-cache saturates; its queue-wait now exceeds even the old 1200 ms timeout, so reverting the timeout alone does not recover. Revert + disable player-core retries (or 4x scale + one of them).

**Minimal sufficient fixes (computed):** `scale_media_cache_2x` + `player_retries_off`; `scale_media_cache_4x` + `player_retries_off`; `scale_media_cache_4x` + `player_retries_1`; `scale_media_cache_4x` + `session_retries_off`; `scale_media_cache_4x` + `rollback_player_core_config`; `player_retries_off` + `rollback_player_core_config`

**Without action:** entry error rate 100% in the judged window.

**Tempting wrong answer:** ['Scale media-cache 2x'] → verifier: **PARTIAL** (entry error rate after replay 100%).

## 11-phantom-drop — Phantom outage: a metric artefact after a pod restart, no user impact

**What happened:** Nothing is broken. sa-3 restarted at 14:01:40 and its per-pod counter is registered lazily, so its series vanished; the summed rate dropped ~25%. Error rates, latency, request rates and the independent docstore session inserts are flat. Correct response: no production change; fix the alert/metric (prime counters at startup, alert on docstore truth).

**Correct response:** no production change (entry error rate 0.0% throughout).

**Tempting wrong answer:** ['Roll back session-api v4.12.1', 'Restart session-api pods'] → verifier: **REJECT** (entry error rate after replay 0%).

## 12-cache-ttl — Cache TTL change collapses hit ratio; the revert alone leaves a retry storm

**What happened:** PR #5188 cut the segment cache TTL, so most requests miss and do origin work; media-cache throughput falls to ~170 req/s, below the ~200 req/s base load. Timeouts trigger x8 layered retries (~1600 req/s). Reverting the TTL restores ~450 req/s, still far below the retry load, so it stays saturated. Revert + disable player-core retries (or 4x scale + one of them).

**Minimal sufficient fixes (computed):** `scale_media_cache_4x` + `rollback_media_cache`; `scale_media_cache_4x` + `player_retries_off`; `rollback_media_cache` + `player_retries_off`

**Without action:** entry error rate 100% in the judged window.

**Tempting wrong answer:** ['Revert segment cache TTL to 3600s'] → verifier: **PARTIAL** (entry error rate after replay 100%).

## 13-cache-pool — Connection-pool cut; revert alone leaves a retry storm

**What happened:** PR #5203 shrank media-cache's origin connection pool, capping throughput at ~160 req/s against ~200 req/s base load. Timeouts trigger x8 layered retries (~1600 req/s). Reverting the pool size alone restores ~450 req/s, below the retry load, so it stays saturated. Revert + disable player-core retries (or 4x scale + one of them).

**Minimal sufficient fixes (computed):** `scale_media_cache_4x` + `rollback_media_cache`; `scale_media_cache_4x` + `player_retries_off`; `rollback_media_cache` + `player_retries_off`

**Without action:** entry error rate 100% in the judged window.

**Tempting wrong answer:** ['Revert max_connections to 512'] → verifier: **PARTIAL** (entry error rate after replay 100%).

## 14-double-fault — COMPLEX: two independent faults at once (both must be fixed)

**What happened:** Two unrelated faults overlap: one meta-index replica uses a key staging revoked (~30% of lookups 401, not retryable), and the post-deploy msg-cleanup job pushes notify-relay over Chatline's quota (~23% 429s). Each alone breaches the SLO, so both the credential and the cleanup job must be fixed.

**Minimal sufficient fixes (computed):** `stop_msg_cleanup` + `rotate_meta_creds`

**Without action:** entry error rate 39% in the judged window.

**Tempting wrong answer:** ['Pause the msg-cleanup job'] → verifier: **REJECT** (entry error rate after replay 30%).

## 15-loud-masks-quiet — COMPLEX: a loud retry storm hides a quiet credential fault

**What happened:** An expired EU key has made ~9% of meta-index lookups fail (401, not retried) since 14:01:10, already above the 5% SLO. At 14:02:00 a media-cache OOM blip starts a metastable retry storm (100% errors) that hides it. Disabling the retries ends the storm but leaves 9% errors; rotating the key alone leaves the storm. Both are needed.

**Minimal sufficient fixes (computed):** `scale_media_cache_4x` + `rotate_meta_creds`; `player_retries_off` + `rotate_meta_creds`

**Without action:** entry error rate 100% in the judged window.

**Tempting wrong answer:** ['Set player-core -> media-cache retries to 0'] → verifier: **REJECT** (entry error rate after replay 9%).
