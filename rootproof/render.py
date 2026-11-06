"""Render an incident simulation into the on-call packet the subject AI sees (and nothing else)."""
from datetime import datetime, timedelta

from incidents import DEPS_TEXT, ENTRY, topo_for
from sim import PAGE_T, run

CLOCK0 = datetime(2026, 11, 6, 14, 0, 0)
SHOWN = ["edge-gw", "session-api", "docstore", "meta-index", "notify-relay", "player-core", "media-cache",
         "vela-origin", "korra-origin", "brio-origin", "clip-preview", "clipstream-origin"]

PROMPT = """You are the on-call SRE for Larkwave, a music streaming service. You were paged at {paged}.
Using ONLY the telemetry below, find the root cause and say exactly what the on-call should do right now to restore service.
If more than one action is needed, list every one, with exact values.

{packet}

End your answer with a JSON block exactly like:
```json
{{"root_cause_service": "...", "mechanism": "...", "actions": ["...", "..."]}}
```"""


def clock(t):
    return (CLOCK0 + timedelta(seconds=t)).strftime("%H:%M:%S")


def packet(inc):
    topo = topo_for(inc)
    rows = run(topo, inc["faults"], ENTRY)
    at = {(r["t"], r["svc"]): r for r in rows}
    out = ["## Service metrics, 15s buckets",
           "req/s = requests received (incl. retries) | p99 = latency incl. queue wait | err = share of responses this service "
           "itself returned as errors (caller-side timeouts are logged by the caller) | util = CPU/worker utilisation"]
    for n in SHOWN:
        s = topo[n]
        out.append(f"\n### {n}")
        for t in range(60, PAGE_T + 1, 15):
            r = at[(t, n)]
            util = min(100, 100 * r["rps"] / s.capacity) if s.queue else min(100, 100 * r["rps"] / s.capacity)
            if inc["id"].startswith("02") and n == "media-cache" and t >= 112:
                util = min(100, 100 * r["rps"] / 180)
            out.append(f"{clock(t)}  req/s={r['rps']:6.0f}  p99={min(r['lat_ms'], 60000):6.0f}ms  err={100 * r['err']:3.0f}%  util={util:3.0f}%")
    out += ["", "## Dependency map", DEPS_TEXT, "", "## Change / event log (newest last)"]
    for t, svc, kind, msg in sorted(inc["events"]):
        when = "yesterday" if t < 0 else clock(t)
        out.append(f"{when:9s}  {svc:17s} {kind:7s} {msg}")
    out += ["", f"## Error log lines by service, {clock(0)}-{clock(PAGE_T)} (count, sample message)"]
    for n, msg in inc["logs"].items():
        cnt = int(sum(at[(t, n)]["rps"] * at[(t, n)]["err"] for t in range(PAGE_T)))
        if msg is None:
            wait = max(at[(t, n)]["backlog"] for t in range(PAGE_T - 15, PAGE_T)) / topo[n].capacity
            out.append(f"{n:17s} {0:8d}  (no error logs; queue-wait gauge {wait:.1f}s)")
        else:
            out.append(f"{n:17s} {cnt:8d}  '{msg}'")
    if "clipstream-origin" not in inc["logs"]:
        cnt = int(sum(at[(t, 'clipstream-origin')]["rps"] * at[(t, 'clipstream-origin')]["err"] for t in range(PAGE_T)))
        out.append(f"{'clipstream-origin':17s} {cnt:8d}  '403 region-locked clip, preview skipped'")
    if inc.get("extra"):
        out += ["", inc["extra"]]
    return "\n".join(out)


def prompt(inc):
    return PROMPT.format(paged=clock(PAGE_T), packet=packet(inc))


if __name__ == "__main__":
    import sys
    from incidents import INCIDENTS
    inc = next(i for i in INCIDENTS if i["id"].startswith(sys.argv[1] if len(sys.argv) > 1 else "01"))
    print(prompt(inc))
