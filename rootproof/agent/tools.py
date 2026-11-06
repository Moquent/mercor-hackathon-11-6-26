"""The on-call tools the subject AI can call. Each reads the seed-0 simulation up to page time only (no future).

Outputs are deterministic text, and each also returns a typed `observed` record the trace verifier uses to decide
whether a cited step actually shows what the AI claims.
"""
from dataclasses import replace

from ..config import PAGE_T
from ..packets import SHOWN
from ..scenario.incidents import topology_for
from ..scenario.topology import DEPS_TEXT, ENTRY
from ..sim import run
from ..timeline import clock

HEALTH_ERR = 0.05      # own error share above which a service reports itself degraded
HEALTH_UTIL = 0.95     # utilisation above which a service reports itself saturated
HEALTH_WAIT_S = 1.0    # queue wait above which a service reports itself saturated

_STR = {"type": "string"}
_REASON = {"type": "string", "description": "Why you are making this call, in one sentence."}


def _fn(name, description, **props):
    props["reason"] = _REASON
    return {"type": "function", "name": name, "description": description, "strict": True,
            "parameters": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}}


SCHEMAS = [
    _fn("list_services", "List every Larkwave service you can inspect."),
    _fn("get_dependencies", "Configured downstream dependencies of a service, with timeouts, retries and call mode.", service=_STR),
    _fn("get_metrics", "15-second metrics for a service from 14:01:00 to the page: req/s received (incl. retries), p99 latency, "
                       "share of responses it returned as errors (includes errors it passes on from its dependencies).", service=_STR),
    _fn("get_logs", "Error log line count and a sample message for a service since 14:00:00.", service=_STR),
    _fn("check_health", "The service's own health probe right now: its own process error rate (excluding dependency failures), "
                        "utilisation and queue wait.", service=_STR),
    _fn("check_connectivity", "Synthetic TCP probe from one service to one of its dependencies over the last 60 s: share of "
                              "connection attempts refused or reset.", src=_STR, dst=_STR),
    _fn("get_changes", "The change / deploy / alert log up to the page, newest last."),
]


class Environment:
    """One incident's observable world at page time."""

    KNOWN = set(SHOWN) | {"sub-poller", "msg-cleanup"}   # services and jobs a root-cause claim can name

    def __init__(self, incident):
        self.incident = incident
        self.topo = topology_for(incident)
        rows = run(self.topo, incident.faults, ENTRY)
        self.at = {(r["t"], r["svc"]): r for r in rows}

    def effective(self, t):
        """Service configs with the faults active at second t applied, plus active link faults."""
        svcs = {n: replace(s, deps=list(s.deps)) for n, s in self.topo.items()}
        links = {}
        for f in self.incident.faults:
            if f.start <= t < f.end:
                if f.link:
                    links[(f.svc, f.link)] = f.value
                else:
                    setattr(svcs[f.svc], f.field, f.value)
        return svcs, links

    def call(self, name, args):
        """Returns (text shown to the AI, typed observation for the verifier)."""
        svc = args.get("service")
        if svc is not None and svc not in self.topo:
            return f"unknown service '{svc}'. Known: {', '.join(SHOWN)}", {"error": "unknown service"}
        return getattr(self, "_" + name)(**{k: v for k, v in args.items() if k != "reason"})

    def _list_services(self):
        return "\n".join(SHOWN), {"services": SHOWN}

    def _get_dependencies(self, service):
        s = self.topo[service]
        if not s.deps:
            text = f"{service}: no downstream dependencies"
        else:
            how = "fallback chain, tried in order" if s.mode == "fallback" else "all required per request"
            text = (f"{service} -> {', '.join(s.deps)} ({how}); timeout {s.timeout_ms:.0f}ms, retries {s.retries}")
        notes = [ln for ln in DEPS_TEXT.splitlines() if service in ln and "->" not in ln]
        return "\n".join([text] + notes), {"service": service, "deps": list(s.deps)}

    def _get_metrics(self, service):
        lines, series = [f"{service} (time, req/s, p99, err)"], []
        for t in range(60, PAGE_T + 1, 15):
            r = self.at[(t, service)]
            lines.append(f"{clock(t)}  req/s={r['rps']:6.0f}  p99={min(r['lat_ms'], 60000):6.0f}ms  err={100 * r['err']:3.0f}%")
            series.append({"t": t, "rps": r["rps"], "lat_ms": r["lat_ms"], "err": r["err"]})
        return "\n".join(lines), {"service": service, "series": series}

    def _get_logs(self, service):
        cnt = int(sum(self.at[(t, service)]["rps"] * self.at[(t, service)]["err"] for t in range(PAGE_T)))
        msg = self.incident.logs.get(service)
        if msg is None:
            text = f"{service}: no error log lines"
            cnt = 0
        else:
            text = f"{service}: {cnt} error lines, sample: '{msg}'"
        return text, {"service": service, "count": cnt, "message": msg}

    def _check_health(self, service):
        svcs, _ = self.effective(PAGE_T - 1)
        s = svcs[service]
        r = self.at[(PAGE_T - 1, service)]
        util = min(1.0, r["rps"] / s.capacity) if s.queue else min(1.0, r["rps"] / s.capacity)
        wait = r["backlog"] / s.capacity
        status = "degraded" if s.err > HEALTH_ERR else "saturated" if util > HEALTH_UTIL or wait > HEALTH_WAIT_S else "healthy"
        text = (f"{service}: {status.upper()}  own_error_rate={100 * s.err:.0f}%  utilisation={100 * util:.0f}%  "
                f"queue_wait={wait:.1f}s  pods ready")
        return text, {"service": service, "status": status, "own_err": s.err, "util": util, "wait_s": wait}

    def _check_connectivity(self, src, dst):
        if src not in self.topo or dst not in self.topo:
            return f"unknown service in probe {src} -> {dst}", {"error": "unknown service"}
        if dst not in self.topo[src].deps:
            return f"{src} has no configured route to {dst}", {"src": src, "dst": dst, "refused": None}
        refused = max(self.effective(t)[1].get((src, dst), 0.0) for t in range(PAGE_T - 60, PAGE_T))
        text = f"{src} -> {dst}: {100 * refused:.0f}% of connection attempts refused or reset (last 60 s)"
        return text, {"src": src, "dst": dst, "refused": refused}

    def _get_changes(self):
        lines = []
        for t, svc, kind, msg in sorted(self.incident.events):
            when = "yesterday" if t < 0 else clock(t)
            lines.append(f"{when:9s}  {svc:17s} {kind:7s} {msg}")
        return "\n".join(lines), {"events": [list(e) for e in sorted(self.incident.events)]}
