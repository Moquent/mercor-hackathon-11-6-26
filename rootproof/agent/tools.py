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
    _fn("resolve_dns", "Resolve a dependency's service name from one service's pods (last 60 s): the record returned, and the share "
                       "of lookups that fail (NXDOMAIN / stale address).", src=_STR, dst=_STR),
    _fn("check_tls", "TLS / mTLS handshake check from one service to one of its dependencies (last 60 s): certificate subjects, "
                     "expiry and the share of handshakes that fail.", src=_STR, dst=_STR),
    _fn("get_proxy_stats", "Sidecar proxy stats for one service's route to a dependency (last 60 s): requests, upstream timeout "
                           "share, latency the proxy path adds, connect failures and TCP retransmits.", src=_STR, dst=_STR),
]

PROBES = {"refuse": "check_connectivity", "dns": "resolve_dns", "tls": "check_tls", "latency": "get_proxy_stats",
          "loss": "get_proxy_stats"}
LINK_TOOLS = {"check_connectivity", "resolve_dns", "check_tls", "get_proxy_stats"}


def focus(tool, args):
    """What a tool call looks at, for the UI cursor: {"nodes": [...], "edge": [src, dst] or None}."""
    args = args if isinstance(args, dict) else {}
    if tool in LINK_TOOLS:
        src, dst = args.get("src"), args.get("dst")
        return {"nodes": [n for n in (src, dst) if n], "edge": [src, dst] if src and dst else None}
    svc = args.get("service")
    return {"nodes": [svc] if svc else [], "edge": None}


def _fake_ip(name, salt=0):
    h = sum((i + 1) * ord(ch) for i, ch in enumerate(name)) + 97 * salt
    return f"10.{40 + h % 20}.{h // 20 % 250}.{h // 5000 % 250 + 2}"


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

    def link_faults(self, t):
        """Active link faults at second t: {(src, dst): (mode, value)}."""
        return {(f.svc, f.link): (f.mode, f.value) for f in self.incident.faults if f.link and f.start <= t < f.end}

    def _link_window(self, src, dst):
        """(mode, worst value) of the src -> dst link fault over the last 60 s before the page, or (None, 0.0)."""
        mode, worst = None, 0.0
        for t in range(PAGE_T - 60, PAGE_T):
            m, v = self.link_faults(t).get((src, dst), (None, 0.0))
            if m is not None and v >= worst:
                mode, worst = m, v
        return mode, worst

    def _route(self, src, dst):
        if src not in self.topo or dst not in self.topo:
            return f"unknown service in probe {src} -> {dst}", {"error": "unknown service"}
        if dst not in self.topo[src].deps:
            return f"{src} has no configured route to {dst}", {"src": src, "dst": dst, "mode": None, "failing": None}
        return None

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
        modes = {f.mode for f in self.incident.faults if f.link and f.svc == src and f.link == dst}
        if modes - {"refuse"}:      # dns / tls / latency / loss faults do not refuse TCP connections
            return (f"{src} -> {dst}: TCP ok, 0% of connection attempts refused (last 60 s)",
                    {"src": src, "dst": dst, "refused": 0.0})
        refused = max(self.effective(t)[1].get((src, dst), 0.0) for t in range(PAGE_T - 60, PAGE_T))
        text = f"{src} -> {dst}: {100 * refused:.0f}% of connection attempts refused or reset (last 60 s)"
        return text, {"src": src, "dst": dst, "refused": refused}

    def _resolve_dns(self, src, dst):
        bad = self._route(src, dst)
        if bad:
            return bad
        mode, v = self._link_window(src, dst)
        name, ip = f"{dst}.svc.larkwave", _fake_ip(dst)
        if mode == "dns":
            text = (f"{src} -> {name}: {100 * v:.0f}% of lookups fail (NXDOMAIN or stale A record {_fake_ip(dst, 1)}, "
                    f"decommissioned); healthy answers: A {ip} TTL 30 (last 60 s)")
            return text, {"src": src, "dst": dst, "mode": "dns", "failing": v, "record": ip, "stale_record": _fake_ip(dst, 1)}
        return (f"{src} -> {name}: A {ip} TTL 30, 0% lookup failures (last 60 s)",
                {"src": src, "dst": dst, "mode": None, "failing": 0.0, "record": ip})

    def _check_tls(self, src, dst):
        bad = self._route(src, dst)
        if bad:
            return bad
        mode, v = self._link_window(src, dst)
        client, server = f"CN={src}-{dst}-client", f"CN={dst}.svc.larkwave"
        if mode == "tls":
            text = (f"{src} -> {dst}: {100 * v:.0f}% of handshakes fail: x509: certificate has expired or is not yet valid "
                    f"(client cert {client}, notAfter 14:02:00); server cert {server} valid 61 days")
            return text, {"src": src, "dst": dst, "mode": "tls", "failing": v, "client_cert": client, "expired": True}
        return (f"{src} -> {dst}: handshake ok (0% failures); client cert {client} valid 43 days, server cert {server} valid 61 days",
                {"src": src, "dst": dst, "mode": None, "failing": 0.0, "client_cert": client, "expired": False})

    def _get_proxy_stats(self, src, dst):
        bad = self._route(src, dst)
        if bad:
            return bad
        mode, v = self._link_window(src, dst)
        to = self.topo[src].timeout_ms
        win = range(PAGE_T - 60, PAGE_T)
        rq = sum(self.at[(t, src)]["rps"] for t in win) / 60
        callee_ms = sum(self.at[(t, dst)]["lat_ms"] for t in win) / 60
        added = v if mode == "latency" else 2000 * v if mode == "loss" else 0.0
        connect_fail = v if mode in ("refuse", "loss") else 0.0
        seen = callee_ms + added
        timeouts = min(1.0, (seen - to) / (0.25 * to)) if seen > to else 0.0
        retrans = v if mode == "loss" else 0.0
        text = (f"{src} -> {dst} sidecar (last 60 s): {rq:.0f} req/s, upstream_rq_timeout {100 * timeouts:.0f}% (timeout {to:.0f}ms), "
                f"proxy-path added latency {added:.0f}ms (upstream service time {callee_ms:.0f}ms), "
                f"connect failures {100 * connect_fail:.0f}%, TCP retransmits {100 * retrans:.0f}% of segments")
        return text, {"src": src, "dst": dst, "mode": mode if mode in ("latency", "loss") else None, "failing": connect_fail,
                      "added_ms": added, "timeout_share": timeouts, "retransmit": retrans}

    def _get_changes(self):
        lines = []
        for t, svc, kind, msg in sorted(self.incident.events):
            when = "yesterday" if t < 0 else clock(t)
            lines.append(f"{when:9s}  {svc:17s} {kind:7s} {msg}")
        return "\n".join(lines), {"events": [list(e) for e in sorted(self.incident.events)]}
