"""The fifteen incidents. Each is data: injected faults, the change/event log, the logs the on-call sees, and the
computed-truth note used by the ground truth document.

Tasks 01-11 are the original traps and controls. 12-13 test the "fixes the trigger, misses the loop" hypothesis.
14-15 are multi-fault cases.
"""
from dataclasses import dataclass, field

from ..sim import Fault
from .topology import topology


@dataclass
class Incident:
    id: str
    title: str
    faults: list
    events: list
    logs: dict
    truth: str
    no_outage: bool = False
    clip_noise: float = 0.0
    sticky: list = field(default_factory=list)
    extra: str = ""


INCIDENT_DATA = [
    dict(
        id="01-vela-402", title="Primary origin billing lapse -> fallback flood",
        faults=[Fault("F1", "vela-origin", "err", 1.0, 110, kind="external")],
        events=[(96, "brio-origin", "vendor", "status page: 'elevated 429s for EU tenants' (posted 13:20, still open)"),
                (180, "player-core", "alert", "PlaybackFailoverExhausted firing"),
                (240, "media-cache", "alert", "OriginFallbackRate > 30%")],
        logs={"vela-origin": "HTTP 402 Payment Required: tenant lw-prod invoice INV-20931 overdue",
              "korra-origin": "media URI unavailable for catalog id (not licensed in region)",
              "brio-origin": "HTTP 429 Too Many Requests (tenant quota 60 req/s)",
              "player-core": "media-cache returned error, attempt n/4",
              "session-api": "player-core call failed: upstream error (attempt 1/2, 2/2)"},
        truth="vela-origin started returning HTTP 402 at 14:01:50 (billing lapse). Its traffic fell through to korra (which misses "
              "unlicensed catalog) and brio (quota 60 req/s -> 429s). brio's 429s are the loudest signal but are a symptom of the load shift. "
              "Restoring vela access ends the incident; raising brio limits does not, because brio and korra still miss part of the catalog.",
    ),
    dict(
        id="02-cache-cpu-limit", title="Persistent capacity cut + retry amplification (needs two actions)",
        faults=[Fault("F1", "media-cache", "capacity", 180, 112, kind="config", change="media-cache-config")],
        events=[(112, "media-cache", "config", "resources.limits.cpu 2000m -> 800m (cost-savings PR #4471, auto-merged)"),
                (150, "media-cache", "infra", "rollout complete, healthy pods 3/3, cpu throttling 91%"),
                (165, "player-core", "deploy", "v2.14.0 rolled out (100%)"),
                (230, "session-api", "alert", "HighErrorRate firing (err% > 20 for 60s)")],
        logs={"player-core": "media-cache request timed out after 1200ms (attempt n/4)",
              "session-api": "call player-core failed: deadline exceeded (attempt 1/2, 2/2)",
              "edge-gw": "upstream session-api returned 504",
              "media-cache": None},
        truth="The CPU limit cut dropped media-cache capacity below normal demand. Timeouts triggered 2x4 layered retries, so media-cache "
              "now receives ~8x the entry rate. Reverting the CPU limit alone restores 450 req/s, still far below the ~1600 req/s retry load, "
              "so the system stays saturated. It needs the revert AND retry amplification removed (or >=4x scale-out).",
    ),
    dict(
        id="03-cache-oom-blip", title="Metastable retry storm after a recovered OOM",
        faults=[Fault("F1", "media-cache", "capacity", 150, 120, 150, kind="transient")],
        events=[(95, "meta-index", "config", "token-cache TTL 300s -> 240s"),
                (120, "media-cache", "infra", "pod mc-2 OOMKilled, healthy pods 3/3 -> 1/3"),
                (150, "media-cache", "infra", "mc-2 replaced, healthy pods 3/3"),
                (165, "player-core", "deploy", "v2.14.0 rolled out (100%)"),
                (230, "session-api", "alert", "HighErrorRate firing (err% > 20 for 60s)")],
        logs={"player-core": "media-cache request timed out after 1200ms (attempt n/4)",
              "session-api": "call player-core failed: deadline exceeded (attempt 1/2, 2/2)",
              "edge-gw": "upstream session-api returned 504",
              "media-cache": None},
        truth="A 30 s OOM blip pushed media-cache into overload. Layered retries (x8) keep offered load above capacity after the pods "
              "recovered: a metastable failure. Restarting/scaling media-cache 2x does nothing; removing the player-core retries drains it.",
    ),
    dict(
        id="04-docstore-poller", title="Cron poll-interval change saturates the database",
        faults=[Fault("F1", "docstore", "extra_rps", 3600, 100, kind="config", change="sub-poller")],
        events=[(100, "sub-poller", "deploy", "v1.8.0: subscription sync interval 60s -> 5s, full scan of subscriptions per worker"),
                (150, "docstore", "infra", "primary CPU 97%, connections 4890/5000"),
                (210, "session-api", "alert", "BotRuntimeSaturated / p99 > 1.5s"),
                (260, "docstore", "alert", "SlowQueries > 2k/min (collection: subscriptions)")],
        logs={"session-api": "docstore query timed out after 1500ms (attempt 1/2, 2/2)",
              "edge-gw": "upstream session-api returned 504",
              "docstore": "slow query: subscriptions.find({}) 1840ms, planSummary: COLLSCAN"},
        truth="sub-poller v1.8.0 multiplied background load on docstore (~3600 extra req/s against 1500 capacity). Restarting docstore "
              "drops the queue for seconds before the poller refills it; 2x scale is not enough. Rolling back/pausing the poller fixes it.",
    ),
    dict(
        id="05-relay-storm", title="Cleanup job hits external rate limit; loud pre-existing noise elsewhere",
        faults=[Fault("F1", "notify-relay", "extra_rps", 1500, 105, kind="config", change="msg-cleanup")],
        events=[(-86400, "clipstream-origin", "note", "known issue LW-3112: clipstream 4xx on region-locked clips (since yesterday)"),
                (100, "session-api", "deploy", "v4.12.0 rolled out; post-deploy msg-cleanup job started (bulk DELETE of stale now-playing messages)"),
                (200, "clip-preview", "alert", "ClipPreviewErrors firing"),
                (230, "session-api", "alert", "InteractionFailures > 25%")],
        logs={"notify-relay": "Chatline API 429 global rate limit, retry_after=1.0s",
              "session-api": "notify-relay send failed: 429 (attempt 1/2, 2/2)",
              "edge-gw": "upstream session-api returned 502",
              "clipstream-origin": "403 region-locked clip, preview skipped",
              "clip-preview": "clipstream fetch failed, preview skipped"},
        clip_noise=0.4,
        truth="The msg-cleanup job started by the v4.12.0 deploy sends ~1500 extra req/s through notify-relay; Chatline's 1000 req/s global "
              "quota rejects ~60% with 429, failing session-api requests. clipstream errors are pre-existing, off the request path. "
              "Rolling back session-api would itself trigger another cleanup run; pausing msg-cleanup fixes it.",
    ),
    dict(
        id="06-meta-creds", title="Credential expiry with a red-herring deploy right after",
        faults=[Fault("F1", "meta-index", "err", 0.7, 100, kind="external")],
        events=[(40, "meta-index", "secret", "staging rotated shared API key lw-meta-key-2 (key is shared with prod)"),
                (140, "session-api", "deploy", "v4.12.0 rolled out (100%): search ranking tweak"),
                (190, "session-api", "alert", "SearchFailures > 30%"),
                (250, "edge-gw", "alert", "SLO burn rate 9x")],
        logs={"meta-index": "upstream auth failed: 401 invalid_client (key lw-meta-key-2)",
              "session-api": "meta-index lookup failed: 401 (attempt 1/2, 2/2)",
              "edge-gw": "upstream session-api returned 502"},
        truth="meta-index began returning 401 at 14:01:40 because staging rotated a key shared with prod. The session-api deploy came 40 s "
              "after errors began and does not touch meta-index; rolling it back changes nothing. Restoring/rotating the credential fixes it.",
    ),
    dict(
        id="07-relay-bad-deploy", title="CONTROL: bad deploy that really is the cause",
        faults=[Fault("F1", "notify-relay", "err", 0.85, 105, kind="config", change="notify-relay-deploy")],
        events=[(105, "notify-relay", "deploy", "v1.9.0 rolled out (100%): new Chatline client library"),
                (170, "session-api", "alert", "InteractionFailures > 25%")],
        logs={"notify-relay": "TypeError: chatline.send is not a function",
              "session-api": "notify-relay send failed: 500 (attempt 1/2, 2/2)",
              "edge-gw": "upstream session-api returned 502"},
        truth="notify-relay v1.9.0 broke sends (TypeError) the moment it rolled out. Rolling it back fixes the incident.",
    ),
    dict(
        id="08-docstore-primary", title="CONTROL: database primary crash",
        faults=[Fault("F1", "docstore", "err", 1.0, 110, kind="transient")],
        events=[(110, "docstore", "infra", "primary ds-0 unreachable (kernel panic); no automatic failover configured"),
                (170, "session-api", "alert", "HighErrorRate firing")],
        logs={"docstore": "connection refused ds-0:27017",
              "session-api": "docstore query failed: ECONNREFUSED (attempt 1/2, 2/2)",
              "edge-gw": "upstream session-api returned 502"},
        truth="The docstore primary crashed. Restarting / failing over docstore fixes it.",
    ),
    dict(
        id="09-cache-node-loss", title="Permanent node loss: reschedule alone and retries-off alone both fail",
        faults=[Fault("F1", "media-cache", "capacity", 150, 110, kind="transient")],
        events=[(110, "media-cache", "infra", "node lw-n7 NotReady (disk pressure); pods mc-1, mc-2 evicted, Pending: 0/9 nodes have free cpu"),
                (120, "media-cache", "infra", "healthy pods 3/3 -> 1/3"),
                (190, "session-api", "alert", "HighErrorRate firing (err% > 20 for 60s)"),
                (250, "media-cache", "infra", "cluster-autoscaler: scale-up blocked, max node count reached for pool cache-pool")],
        logs={"player-core": "media-cache request timed out after 1200ms (attempt n/4)",
              "session-api": "call player-core failed: deadline exceeded (attempt 1/2, 2/2)",
              "edge-gw": "upstream session-api returned 504",
              "media-cache": None},
        truth="A node failure left media-cache at 1/3 pods (150 req/s, below the 200 req/s base load). Timeouts triggered x8 layered retries. "
              "Rescheduling the pods (restart/replace) restores 450 req/s but the retry load (~1600 req/s) keeps it saturated; disabling retries "
              "alone leaves 150 req/s capacity under 200+ req/s demand. It needs both (or 4x scale + one of them).",
    ),
    dict(
        id="10-timeout-config", title="Timeout set below normal latency; revert alone leaves a retry storm",
        faults=[Fault("F1", "player-core", "timeout_ms", 150, 110, kind="config", change="player-core-config")],
        events=[(110, "player-core", "config", "media-cache client timeout 1200ms -> 150ms (PR #5120 'fail fast on slow cache')"),
                (165, "media-cache", "infra", "HPA at max replicas 3/3"),
                (220, "session-api", "alert", "HighErrorRate firing (err% > 20 for 60s)")],
        logs={"player-core": "media-cache request timed out after 150ms (attempt n/4)",
              "session-api": "call player-core failed: deadline exceeded (attempt 1/2, 2/2)",
              "edge-gw": "upstream session-api returned 504",
              "media-cache": None},
        truth="PR #5120 set player-core's media-cache timeout (150 ms) below media-cache's normal latency (~160 ms), so most calls time out "
              "and are retried x4 (x8 with session-api). media-cache saturates; its queue-wait now exceeds even the old 1200 ms timeout, "
              "so reverting the timeout alone does not recover. Revert + disable player-core retries (or 4x scale + one of them).",
    ),
    dict(
        id="11-phantom-drop", title="Phantom outage: a metric artefact after a pod restart, no user impact",
        faults=[], no_outage=True,
        events=[(70, "session-api", "deploy", "v4.12.1 rolled out (100%): log level tweak"),
                (100, "session-api", "infra", "pod sa-3 OOMKilled and restarted (1 of 4 pods)"),
                (190, "player-core", "alert", "PlaybackStartsDropped firing (starts/min -26% vs 1h ago)"),
                (290, "player-core", "alert", "PlaybackStartsDropped still firing")],
        logs={"session-api": "pod sa-3 started; metrics registry initialised lazily on first labelled event"},
        extra=("## Business metrics\n"
               "playback_starts_total, per-pod rate (starts/s), scraped from each session-api pod's /metrics\n"
               "time      sa-1  sa-2  sa-3  sa-4  sum\n" + "\n".join(
                   f"14:0{m // 60}:{m % 60:02d}  {31 + (m // 30 * 7) % 3:4d}  {30 + (m // 30 * 5) % 4:4d}  {(30 + (m // 30 * 2) % 3) if m < 100 else 0:4d}  "
                   f"{32 - (m // 30 * 11) % 3:4d}  {(31 + (m // 30 * 7) % 3) + (30 + (m // 30 * 5) % 4) + ((30 + (m // 30 * 2) % 3) if m < 100 else 0) + (32 - (m // 30 * 11) % 3):4d}"
                   for m in range(60, 301, 30)) +
               "\n\nplayback_sessions collection, documents inserted per minute (docstore, independent of /metrics)\n" + "\n".join(
                   f"14:0{m}:00  {7380 + (m * 37) % 90}" for m in range(0, 5))),
        truth="Nothing is broken. sa-3 restarted at 14:01:40 and its per-pod counter is registered lazily, so its series vanished; the "
              "summed rate dropped ~25%. Error rates, latency, request rates and the independent docstore session inserts are flat. "
              "Correct response: no production change; fix the alert/metric (prime counters at startup, alert on docstore truth).",
    )
,
    dict(
        id="12-cache-ttl", title="Cache TTL change collapses hit ratio; the revert alone leaves a retry storm",
        faults=[Fault("F1", "media-cache", "capacity", 170, 108, kind="config", change="media-cache-config")],
        events=[(95, "meta-index", "deploy", "v3.4.2 rolled out (100%): ranking cache warmup"),
                (108, "media-cache", "config", "segment cache TTL 3600s -> 60s (PR #5188 'fresher manifests')"),
                (130, "media-cache", "metric", "cache hit ratio 94% -> 31%; origin fetch per request 0.06 -> 0.69"),
                (225, "session-api", "alert", "HighErrorRate firing (err% > 20 for 60s)")],
        logs={"player-core": "media-cache request timed out after 1200ms (attempt n/4)",
              "session-api": "call player-core failed: deadline exceeded (attempt 1/2, 2/2)",
              "edge-gw": "upstream session-api returned 504",
              "media-cache": None},
        truth="PR #5188 cut the segment cache TTL, so most requests miss and do origin work; media-cache throughput falls to ~170 req/s, "
              "below the ~200 req/s base load. Timeouts trigger x8 layered retries (~1600 req/s). Reverting the TTL restores ~450 req/s, "
              "still far below the retry load, so it stays saturated. Revert + disable player-core retries (or 4x scale + one of them).",
    ),
    dict(
        id="13-cache-pool", title="Connection-pool cut; revert alone leaves a retry storm",
        faults=[Fault("F1", "media-cache", "capacity", 160, 115, kind="config", change="media-cache-config")],
        events=[(115, "media-cache", "config", "origin connection pool max_connections 512 -> 96 (PR #5203 'reduce vela socket count')"),
                (140, "media-cache", "metric", "pool wait p99 0ms -> 2.8s, pool in-use 96/96"),
                (175, "notify-relay", "deploy", "v1.9.2 rolled out (100%): retry jitter"),
                (235, "session-api", "alert", "HighErrorRate firing (err% > 20 for 60s)")],
        logs={"player-core": "media-cache request timed out after 1200ms (attempt n/4)",
              "session-api": "call player-core failed: deadline exceeded (attempt 1/2, 2/2)",
              "edge-gw": "upstream session-api returned 504",
              "media-cache": None},
        truth="PR #5203 shrank media-cache's origin connection pool, capping throughput at ~160 req/s against ~200 req/s base load. "
              "Timeouts trigger x8 layered retries (~1600 req/s). Reverting the pool size alone restores ~450 req/s, below the retry load, "
              "so it stays saturated. Revert + disable player-core retries (or 4x scale + one of them).",
    ),
    dict(
        id="14-double-fault", title="COMPLEX: two independent faults at once (both must be fixed)",
        faults=[Fault("F1", "meta-index", "err", 0.3, 100, kind="external"),
                Fault("F2", "notify-relay", "extra_rps", 1300, 130, kind="config", change="msg-cleanup")],
        sticky=["meta-index"],
        events=[(98, "meta-index", "secret", "staging rotated API key lw-meta-key-2; prod replica mi-1 of 3 still uses it"),
                (125, "session-api", "deploy", "v4.12.0 rolled out; post-deploy msg-cleanup job started (bulk DELETE of stale now-playing messages)"),
                (200, "session-api", "alert", "InteractionFailures > 25%"),
                (240, "session-api", "alert", "SearchFailures > 15%")],
        logs={"meta-index": "upstream auth failed: 401 invalid_client (key lw-meta-key-2, replica mi-1)",
              "notify-relay": "Chatline API 429 global rate limit, retry_after=1.0s",
              "session-api": "dependency call failed (meta-index 401 / notify-relay 429) (attempt 1/2, 2/2)",
              "edge-gw": "upstream session-api returned 502"},
        truth="Two unrelated faults overlap: one meta-index replica uses a key staging revoked (~30% of lookups 401, not retryable), and "
              "the post-deploy msg-cleanup job pushes notify-relay over Chatline's quota (~23% 429s). Each alone breaches the SLO, so both "
              "the credential and the cleanup job must be fixed.",
    ),
    dict(
        id="15-loud-masks-quiet", title="COMPLEX: a loud retry storm hides a quiet credential fault",
        faults=[Fault("F1", "meta-index", "err", 0.09, 70, kind="external"),
                Fault("F2", "media-cache", "capacity", 150, 120, 150, kind="transient")],
        sticky=["meta-index"],
        events=[(70, "meta-index", "secret", "API key lw-meta-key-3 expired for EU shard (renewal job failed silently)"),
                (120, "media-cache", "infra", "pod mc-2 OOMKilled, healthy pods 3/3 -> 1/3"),
                (150, "media-cache", "infra", "mc-2 replaced, healthy pods 3/3"),
                (230, "session-api", "alert", "HighErrorRate firing (err% > 20 for 60s)")],
        logs={"meta-index": "upstream auth failed: 401 key expired (lw-meta-key-3, shard eu-1)",
              "player-core": "media-cache request timed out after 1200ms (attempt n/4)",
              "session-api": "call player-core failed: deadline exceeded (attempt 1/2, 2/2)",
              "edge-gw": "upstream session-api returned 504",
              "media-cache": None},
        truth="An expired EU key has made ~9% of meta-index lookups fail (401, not retried) since 14:01:10, already above the 5% SLO. "
              "At 14:02:00 a media-cache OOM blip starts a metastable retry storm (100% errors) that hides it. Disabling the retries "
              "ends the storm but leaves 9% errors; rotating the key alone leaves the storm. Both are needed.",
    ),
]


INCIDENTS = [Incident(**d) for d in INCIDENT_DATA]


def topology_for(incident):
    """The shared topology with this incident's per-run overrides applied."""
    topo = topology()
    if incident.clip_noise:
        topo["clipstream-origin"].err = incident.clip_noise
    for svc in incident.sticky:
        topo[svc].sticky = True
    return topo


# Network incidents for the investigation track: the callee is healthy, the link to it is not.
NET_INCIDENT_DATA = [
    dict(
        id="16-link-partition", title="NET: a NetworkPolicy change cuts player-core off from a healthy media-cache",
        faults=[Fault("F1", "player-core", "link", 0.92, 110, kind="config", change="netpolicy", link="media-cache")],
        events=[(95, "media-cache", "deploy", "v3.2.0 rolled out (100%): eviction tuning"),
                (110, "player-core", "config", "NetworkPolicy np-player-egress updated (PR #5310: restrict egress to labelled namespaces)"),
                (200, "session-api", "alert", "HighErrorRate firing (err% > 20 for 60s)")],
        logs={"player-core": "media-cache request failed: upstream connect error or disconnect/reset before headers (attempt n/4)",
              "session-api": "call player-core failed: upstream error (attempt 1/2, 2/2)",
              "edge-gw": "upstream session-api returned 502",
              "media-cache": None},
        truth="The NetworkPolicy update at 14:01:50 blocks ~92% of player-core -> media-cache connections. media-cache itself is healthy "
              "(0% own errors; its traffic drops because requests never arrive). The media-cache deploy at 14:01:35 is a red herring. "
              "Rolling back the NetworkPolicy ends the incident; rolling back or restarting media-cache does nothing.",
    ),
    dict(
        id="17-origin-partition", title="NET: media-cache loses its route to vela-origin; fallback floods the backups",
        faults=[Fault("F1", "media-cache", "link", 1.0, 105, kind="config", change="netpolicy", link="vela-origin")],
        events=[(80, "vela-origin", "vendor", "status page: 'degraded performance in us-east' (posted 13:10, resolved 13:40)"),
                (105, "media-cache", "config", "egress firewall rule set fw-cache-egress v12 applied (CIDR cleanup)"),
                (220, "media-cache", "alert", "OriginFallbackRate > 30%")],
        logs={"media-cache": "origin vela-origin: connect timeout / connection refused, falling back",
              "brio-origin": "HTTP 429 Too Many Requests (tenant quota 60 req/s)",
              "korra-origin": "media URI unavailable for catalog id (not licensed in region)",
              "player-core": "media-cache returned error, attempt n/4",
              "session-api": "player-core call failed: upstream error (attempt 1/2, 2/2)"},
        truth="The egress firewall change at 14:01:45 blocks every media-cache -> vela-origin connection. vela-origin is healthy (its "
              "traffic just stops). Traffic falls through to korra (misses) and brio (60 req/s quota, 429s). Rolling back the firewall "
              "rule ends the incident; vela-origin needs no action and raising brio's quota does not fix it.",
    ),
]

NET_INCIDENTS = [Incident(**d) for d in NET_INCIDENT_DATA]
