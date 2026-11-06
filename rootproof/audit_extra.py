"""A second hand audit of Jev's extraction, on repeat attempts (runs 2-5).

AUDIT in labels.py covers only the official first attempt (run 1). AUDIT_EXTRA is a sample of 20 (task, run) pairs
drawn with random.Random(0).sample over the 60 pairs (15 tasks x runs 2-5, task-sorted). A reviewer read only the
subject AI's answer text (not the telemetry, the answer key or Jev's output) and wrote down:

  actions: the catalog keys the answer recommends doing now (things it says not to do are not counted);
  roots:   the service(s) the answer names as the root cause, as exact names from topology.SERVICES.

Judgement calls: 04/run3 declares "docstore (overloaded by the sub-poller)" but its prose names the sub-poller deploy
as the root cause, so roots is sub-poller. A "global Chatline outbound limit" is rate limiting, not a quota request.
"""

AUDIT_EXTRA = {
    ("01-vela-402", 4): {"actions": {"restore_vela"}, "roots": ["vela-origin"]},
    ("02-cache-cpu-limit", 4): {"actions": {"rollback_media_cache", "player_retries_off", "session_retries_off"}, "roots": ["media-cache"]},
    ("03-cache-oom-blip", 2): {"actions": {"player_retries_off", "session_retries_off"}, "roots": ["media-cache"]},
    ("04-docstore-poller", 3): {"actions": {"rollback_sub_poller"}, "roots": ["sub-poller"]},
    ("05-relay-storm", 2): {"actions": {"stop_msg_cleanup"}, "roots": ["session-api"]},
    ("05-relay-storm", 4): {"actions": {"stop_msg_cleanup"}, "roots": ["notify-relay"]},
    ("05-relay-storm", 5): {"actions": {"stop_msg_cleanup"}, "roots": ["notify-relay"]},
    ("06-meta-creds", 4): {"actions": {"rotate_meta_creds"}, "roots": ["meta-index"]},
    ("07-relay-bad-deploy", 2): {"actions": {"rollback_notify_relay"}, "roots": ["notify-relay"]},
    ("07-relay-bad-deploy", 3): {"actions": {"rollback_notify_relay"}, "roots": ["notify-relay"]},
    ("07-relay-bad-deploy", 4): {"actions": {"rollback_notify_relay"}, "roots": ["notify-relay"]},
    ("08-docstore-primary", 4): {"actions": {"restart_docstore"}, "roots": ["docstore"]},
    ("08-docstore-primary", 5): {"actions": {"restart_docstore"}, "roots": ["docstore"]},
    ("09-cache-node-loss", 2): {"actions": {"player_retries_off", "session_retries_off", "restart_media_cache"}, "roots": ["media-cache"]},
    ("10-timeout-config", 3): {"actions": {"rollback_player_core_config", "player_retries_off", "session_retries_off"}, "roots": ["player-core"]},
    ("11-phantom-drop", 5): {"actions": set(), "roots": ["session-api"]},
    ("13-cache-pool", 2): {"actions": {"rollback_media_cache"}, "roots": ["media-cache"]},
    ("14-double-fault", 2): {"actions": {"stop_msg_cleanup", "rotate_meta_creds"}, "roots": ["meta-index", "notify-relay"]},
    ("14-double-fault", 4): {"actions": {"stop_msg_cleanup", "rotate_meta_creds"}, "roots": ["session-api", "meta-index"]},
    ("15-loud-masks-quiet", 2): {"actions": {"player_retries_off", "session_retries_off", "rotate_meta_creds"}, "roots": ["media-cache"]},
}
