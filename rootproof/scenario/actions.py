"""The action catalog: every remediation the verifier can replay.

Jev answers one yes/no question per entry ("does the recommendation include ...?"). Selected actions are compiled
to replay operations (ops). An entry with no ops cannot be replayed and only counts as a recognised action.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    key: str
    description: str
    ops: tuple


CATALOG = {
    "restart_media_cache":   ("Restart, redeploy, reschedule or replace media-cache pods, or bring media-cache back to its full healthy pod count (e.g. fix/add nodes so evicted pods can schedule)", [("restart", "media-cache")]),
    "scale_media_cache_2x":  ("Add media-cache replicas/capacity BEYOND its normal size, by about 2x or an unspecified amount (not just restoring missing pods)", [("scale", "media-cache", 2)]),
    "scale_media_cache_4x":  ("Add media-cache capacity to 4x or more of its normal size", [("scale", "media-cache", 4)]),
    "rollback_media_cache":  ("Roll back or revert the recent media-cache configuration change (CPU limit, cache TTL, connection pool, etc.)", [("rollback", "media-cache-config")]),
    "player_retries_off":    ("Set the player-core -> media-cache retries to 0 (disable them)", [("set", "player-core", "retries", 0)]),
    "player_retries_1":      ("Reduce the player-core -> media-cache retries to 1 or 2 (not zero)", [("set", "player-core", "retries", 1)]),
    "session_retries_off":   ("Set session-api retries to 0 (disable them)", [("set", "session-api", "retries", 0)]),
    "restore_vela":          ("Restore vela-origin access (fix billing/account/credentials with the vela provider)", [("restore", "vela-origin")]),
    "scale_brio":            ("Raise the brio-origin rate limit or add brio capacity", [("scale", "brio-origin", 10)]),
    "restart_docstore":      ("Restart or fail over docstore", [("restart", "docstore")]),
    "scale_docstore_2x":     ("Scale docstore up (about 2x, or an unspecified amount)", [("scale", "docstore", 2)]),
    "rollback_sub_poller":   ("Roll back or pause the sub-poller change / cron", [("rollback", "sub-poller")]),
    "stop_msg_cleanup":      ("Pause, stop or throttle the msg-cleanup job", [("rollback", "msg-cleanup")]),
    "rollback_session_api":  ("Roll back the session-api deploy", [("rollback", "session-api-deploy")]),
    "rollback_notify_relay": ("Roll back the notify-relay deploy", [("rollback", "notify-relay-deploy")]),
    "fix_clipstream":        ("Restart, disable or fix clip-preview / clipstream-origin", [("restart", "clipstream-origin")]),
    "rotate_meta_creds":     ("Rotate or restore the meta-index API credentials", [("restore", "meta-index")]),
    "rollback_meta_index":   ("Roll back the meta-index deploy", [("rollback", "meta-index-deploy")]),
    "rollback_player_core_config": ("Set the player-core -> media-cache timeout back to its previous value (1200ms), i.e. revert the player-core timeout change", [("rollback", "player-core-config")]),
    "restart_session_api":   ("Restart or roll-restart session-api pods", [("restart", "session-api")]),
    "request_chatline_quota": ("Ask Chatline for a higher API quota (external, not actionable now)", []),
}

# Each incident: faults (ground truth mechanism), a change/event log, alerts and log templates for the packet.
# "minute" offsets are seconds of sim time; packet clock starts at 14:00:00.


ACTIONS = {key: Action(key, description, tuple(ops)) for key, (description, ops) in CATALOG.items()}

# Network actions, used only by the investigation track (incidents with link faults). Kept out of ACTIONS so the
# Jev questions for the original fifteen incidents, and therefore the cached answers, stay byte-identical.
NET_CATALOG = {
    "rollback_netpolicy": ("Roll back or revert the recent NetworkPolicy / firewall / egress or network rule change", [("rollback", "netpolicy")]),
}
NET_ACTIONS = {key: Action(key, description, tuple(ops)) for key, (description, ops) in NET_CATALOG.items()}

# Fixes for the link-mode incidents (18-21). Kept out of NET_CATALOG / iteration over ALL_ACTIONS on purpose: Jev is asked
# one question per catalog entry, so adding them there would change the (cached) questions for every recorded answer.
NET_EXTRA_CATALOG = {
    "fix_dns_record":          ("Fix or revert the DNS record / DNS migration so meta-index resolves to the right address again "
                                "(e.g. restore the old record, flush the stale entry)", [("rollback", "dns-migration")]),
    "renew_tls_cert":          ("Renew, reissue or replace the expired mTLS client certificate that session-api presents to docstore",
                                [("restore", "session-api")]),
    "rollback_sidecar_config": ("Roll back or revert the session-api sidecar / service-mesh proxy configuration change "
                                "(e.g. the added ext_authz filter on its player-core route)", [("rollback", "sidecar-config")]),
    "fix_network_path":        ("Fix the network path between media-cache and vela-origin (drain or replace the faulty switch "
                                "port / NIC, reroute the traffic)", [("restore", "media-cache")]),
}
NET_EXTRA_ACTIONS = {key: Action(key, description, tuple(ops)) for key, (description, ops) in NET_EXTRA_CATALOG.items()}


class _Catalog(dict):
    """A catalog whose lookups also find NET_EXTRA_ACTIONS (so replay can apply them) while iteration does not."""

    def __missing__(self, key):
        return NET_EXTRA_ACTIONS[key]


ALL_ACTIONS = _Catalog(ACTIONS | NET_ACTIONS)
NET_EXTRA_INCIDENTS = ("18-dns-stale-record", "19-tls-cert-expiry", "20-sidecar-latency", "21-packet-loss")


def catalog_for(incident_id):
    """The action catalog to ask Jev about for one incident: the link-mode incidents also get NET_EXTRA_ACTIONS."""
    return ALL_ACTIONS | NET_EXTRA_ACTIONS if incident_id in NET_EXTRA_INCIDENTS else ALL_ACTIONS
