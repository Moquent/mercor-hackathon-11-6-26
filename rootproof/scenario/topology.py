"""Larkwave: a fictional music-streaming platform. One shared service graph used by every incident.

Every name here is invented. The incident patterns (provider outage with fallback load shift, metastable retry
storms, poller load, rate-limit storms, credential expiry, bad deploys) are common to streaming backends.
"""
from ..sim import Svc

ENTRY = "edge-gw"


def topology():
    return {
        "edge-gw":       Svc("edge-gw", 5000, 5, ["session-api"], timeout_ms=3000),
        "session-api":   Svc("session-api", 3000, 10, ["docstore", "meta-index", "notify-relay", "player-core"],
                             timeout_ms=1500, retries=1),
        "docstore":      Svc("docstore", 1500, 4, extra_rps=100),
        "meta-index":    Svc("meta-index", 2000, 30),
        "notify-relay":  Svc("notify-relay", 1000, 20, queue=False),
        "player-core":   Svc("player-core", 1500, 20, ["media-cache"], timeout_ms=1200, retries=3),
        "media-cache":   Svc("media-cache", 450, 60, ["vela-origin", "korra-origin", "brio-origin"],
                             mode="fallback", timeout_ms=800, sticky=True),
        "vela-origin":   Svc("vela-origin", 2000, 50),
        "korra-origin":  Svc("korra-origin", 600, 150, err=0.4),
        "brio-origin":   Svc("brio-origin", 60, 200, err=0.25, queue=False),
        "clip-preview":  Svc("clip-preview", 400, 90, ["clipstream-origin"], timeout_ms=2000, extra_rps=40),
        "clipstream-origin": Svc("clipstream-origin", 400, 300, err=0.03),
    }


DEPS_TEXT = """edge-gw      -> session-api                                   timeout 3000ms, retries 0
session-api  -> docstore, meta-index, notify-relay, player-core  timeout 1500ms, retries 1 (all four required per request)
player-core  -> media-cache                                   timeout 1200ms, retries 3
media-cache  -> vela-origin, then korra-origin, then brio-origin (fallback chain on miss/error), timeout 800ms
clip-preview -> clipstream-origin  (background preview generator, not on the listener request path)
docstore also serves the sub-poller cron (subscription sync)
notify-relay forwards to the external Chatline API (global quota: 1000 req/s, excess -> 429)"""


# Services the subject AI can blame (Jev choice) - the visible components.
SERVICES = ["edge-gw", "session-api", "docstore", "meta-index", "notify-relay", "player-core", "media-cache",
            "vela-origin", "korra-origin", "brio-origin", "clip-preview", "clipstream-origin", "sub-poller", "msg-cleanup"]
