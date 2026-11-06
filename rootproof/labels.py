"""Hand-written labels that test the verifier itself.

REFERENCE: a correct diagnosis and fix per task, which must be ACCEPTed.
TRAP: the persuasive wrong answer each task is built around, which must not be accepted.
AUDIT: which catalog actions a reviewer says the official first attempt (run 1) recommends. It measures Jev's extraction.
"""
import json


def _answer(service, mechanism, *actions):
    payload = {"root_cause_service": service, "mechanism": mechanism, "actions": list(actions)}
    return "Diagnosis.\n```json\n" + json.dumps(payload) + "\n```"


REFERENCE = {
    "01-vela-402": _answer("vela-origin", "vela returns 402 after billing lapse; traffic falls through to korra/brio", "Pay invoice INV-20931 and restore vela-origin access"),
    "02-cache-cpu-limit": _answer("media-cache", "CPU limit cut dropped capacity; layered retries keep it saturated", "Revert media-cache CPU limit to 2000m", "Set player-core -> media-cache retries to 0"),
    "03-cache-oom-blip": _answer("media-cache", "OOM blip triggered a metastable retry storm", "Set player-core -> media-cache retries to 0"),
    "04-docstore-poller": _answer("sub-poller", "poll interval 60s -> 5s with full scans saturates docstore", "Roll back sub-poller v1.8.0"),
    "05-relay-storm": _answer("msg-cleanup", "post-deploy cleanup job exceeds Chatline global quota", "Pause the msg-cleanup job"),
    "06-meta-creds": _answer("meta-index", "shared key rotated by staging; prod gets 401", "Restore the valid meta-index API credential"),
    "07-relay-bad-deploy": _answer("notify-relay", "v1.9.0 broke sends", "Roll back notify-relay v1.9.0"),
    "08-docstore-primary": _answer("docstore", "primary crashed", "Fail over docstore to a healthy replica"),
    "09-cache-node-loss": _answer("media-cache", "node loss left 1/3 pods; retries keep it saturated", "Add nodes so the evicted media-cache pods reschedule (3/3)", "Set player-core -> media-cache retries to 0"),
    "10-timeout-config": _answer("player-core", "timeout set below normal latency; retry storm", "Set player-core -> media-cache timeout back to 1200ms", "Set player-core -> media-cache retries to 0"),
    "11-phantom-drop": _answer("session-api", "metric artefact after sa-3 restart; no user impact", "No production change; prime the counter at startup and fix the alert"),
    "12-cache-ttl": _answer("media-cache", "TTL cut collapsed hit ratio; retries keep it saturated", "Revert segment cache TTL to 3600s", "Set player-core -> media-cache retries to 0"),
    "13-cache-pool": _answer("media-cache", "pool cut capped throughput; retries keep it saturated", "Revert max_connections to 512", "Set player-core -> media-cache retries to 0"),
    "14-double-fault": _answer("meta-index", "revoked key on one replica plus cleanup job over Chatline quota", "Restore the valid meta-index API key on mi-1", "Pause the msg-cleanup job"),
    "15-loud-masks-quiet": _answer("meta-index and media-cache", "expired EU meta-index key (since 14:01:10) plus a metastable media-cache retry storm", "Set player-core -> media-cache retries to 0", "Rotate the expired meta-index API key lw-meta-key-3"),
}

TRAP = {
    "01-vela-402": _answer("brio-origin", "brio rate limiting (429) is failing playback", "Raise the brio-origin rate limit / ask brio for more quota"),
    "02-cache-cpu-limit": _answer("media-cache", "CPU limit cut", "Revert media-cache CPU limit to 2000m"),
    "03-cache-oom-blip": _answer("media-cache", "pod OOM", "Restart the media-cache pods"),
    "04-docstore-poller": _answer("docstore", "database overloaded", "Restart docstore"),
    "05-relay-storm": _answer("clipstream-origin", "clipstream 403s", "Restart clip-preview and clipstream-origin"),
    "06-meta-creds": _answer("session-api", "v4.12.0 deploy broke search", "Roll back the session-api v4.12.0 deploy"),
    "07-relay-bad-deploy": _answer("session-api", "session-api failing", "Restart session-api pods"),
    "08-docstore-primary": _answer("session-api", "session-api retries", "Set session-api retries to 0"),
    "09-cache-node-loss": _answer("media-cache", "too many retries", "Set player-core -> media-cache retries to 0"),
    "10-timeout-config": _answer("media-cache", "media-cache got slow", "Scale media-cache 2x"),
    "11-phantom-drop": _answer("session-api", "sa-3 OOM and the v4.12.1 deploy dropped playback starts", "Roll back session-api v4.12.1", "Restart session-api pods"),
    "12-cache-ttl": _answer("media-cache", "TTL cut", "Revert segment cache TTL to 3600s"),
    "13-cache-pool": _answer("media-cache", "pool cut", "Revert max_connections to 512"),
    "14-double-fault": _answer("notify-relay", "cleanup job over Chatline quota", "Pause the msg-cleanup job"),
    "15-loud-masks-quiet": _answer("media-cache", "metastable retry storm", "Set player-core -> media-cache retries to 0"),
}

AUDIT = {
    "01-vela-402": {"restore_vela"},
    "02-cache-cpu-limit": {"rollback_media_cache"},
    "03-cache-oom-blip": {"player_retries_off", "session_retries_off"},
    "04-docstore-poller": {"rollback_sub_poller"},
    "05-relay-storm": {"stop_msg_cleanup"},
    "06-meta-creds": {"rotate_meta_creds"},
    "07-relay-bad-deploy": {"rollback_notify_relay"},
    "08-docstore-primary": {"restart_docstore", "session_retries_off"},
    "09-cache-node-loss": {"player_retries_off", "session_retries_off", "restart_media_cache"},
    "10-timeout-config": {"player_retries_off", "rollback_player_core_config"},
    "11-phantom-drop": set(),
    "12-cache-ttl": {"rollback_media_cache", "player_retries_off", "session_retries_off"},
    "13-cache-pool": {"rollback_media_cache"},
    "14-double-fault": {"rotate_meta_creds", "stop_msg_cleanup"},
    "15-loud-masks-quiet": {"player_retries_off", "session_retries_off", "rotate_meta_creds"},
}
