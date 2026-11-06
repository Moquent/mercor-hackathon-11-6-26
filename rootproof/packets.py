"""Renders an incident into the on-call packet the subject AI sees. Nothing else from the simulation leaks in."""
from .config import PAGE_T
from .scenario.incidents import topology_for
from .scenario.topology import DEPS_TEXT, ENTRY
from .sim import run
from .timeline import clock

PROMPT = """You are the on-call SRE for Larkwave, a music streaming service. You were paged at {paged}.
Using ONLY the telemetry below, find the root cause and say exactly what the on-call should do right now to restore service.
If more than one action is needed, list every one, with exact values.

{packet}

End your answer with a JSON block exactly like:
```json
{{"root_cause_service": "...", "mechanism": "...", "actions": ["...", "..."]}}
```"""

SHOWN = ["edge-gw", "session-api", "docstore", "meta-index", "notify-relay", "player-core", "media-cache",
         "vela-origin", "korra-origin", "brio-origin", "clip-preview", "clipstream-origin"]


def _metric_lines(incident, topo, at):
    lines = ["## Service metrics, 15s buckets",
             "req/s = requests received (incl. retries) | p99 = latency incl. queue wait | err = share of responses this service "
             "itself returned as errors (caller-side timeouts are logged by the caller) | util = CPU/worker utilisation"]
    for n in SHOWN:
        capacity = topo[n].capacity
        lines.append(f"\n### {n}")
        for t in range(60, PAGE_T + 1, 15):
            r = at[(t, n)]
            util = min(100, 100 * r["rps"] / capacity)
            if incident.id.startswith("02") and n == "media-cache" and t >= 112:
                util = min(100, 100 * r["rps"] / 180)   # the CPU limit cut is in effect from 112 s
            lines.append(f"{clock(t)}  req/s={r['rps']:6.0f}  p99={min(r['lat_ms'], 60000):6.0f}ms  "
                         f"err={100 * r['err']:3.0f}%  util={util:3.0f}%")
    return lines


def _log_lines(incident, topo, at):
    lines = ["", "## Dependency map", DEPS_TEXT, "", "## Change / event log (newest last)"]
    for t, svc, kind, msg in sorted(incident.events):
        when = "yesterday" if t < 0 else clock(t)
        lines.append(f"{when:9s}  {svc:17s} {kind:7s} {msg}")
    lines += ["", f"## Error log lines by service, {clock(0)}-{clock(PAGE_T)} (count, sample message)"]
    for n, msg in incident.logs.items():
        cnt = int(sum(at[(t, n)]["rps"] * at[(t, n)]["err"] for t in range(PAGE_T)))
        if msg is None:
            wait = max(at[(t, n)]["backlog"] for t in range(PAGE_T - 15, PAGE_T)) / topo[n].capacity
            lines.append(f"{n:17s} {0:8d}  (no error logs; queue-wait gauge {wait:.1f}s)")
        else:
            lines.append(f"{n:17s} {cnt:8d}  '{msg}'")
    if "clipstream-origin" not in incident.logs:
        cnt = int(sum(at[(t, 'clipstream-origin')]["rps"] * at[(t, 'clipstream-origin')]["err"] for t in range(PAGE_T)))
        lines.append(f"{'clipstream-origin':17s} {cnt:8d}  '403 region-locked clip, preview skipped'")
    if incident.extra:
        lines += ["", incident.extra]
    return lines


def packet(incident):
    topo = topology_for(incident)
    rows = run(topo, incident.faults, ENTRY)
    at = {(r["t"], r["svc"]): r for r in rows}
    return "\n".join(_metric_lines(incident, topo, at) + _log_lines(incident, topo, at))


def prompt(incident):
    return PROMPT.format(paged=clock(PAGE_T), packet=packet(incident))
