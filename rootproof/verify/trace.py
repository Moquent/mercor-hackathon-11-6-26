"""Trace verification: every claim in an investigated answer is checked against the steps the AI actually took.

For each typed claim:
  grounded    some step in the trace inspected that service (or, for "unreachable", probed that link)
  supported   a step the claim cites shows what the claim says
  true        the simulation agrees
Label (worst first): FALSE, UNGROUNDED, UNSUPPORTED, VERIFIED.

Missed checks: a faulty link never probed, a root never health-checked, a true-path service never inspected at all,
each with the output that check would have shown.
The first wrong step is the earliest step a non-verified claim rests on (or the final answer, when it cites nothing).
"""
from ..agent.tools import HEALTH_ERR, Environment
from ..config import PAGE_T
from .chain import failure_onset, true_graph
from .claims import LOAD_SOURCES
from .extract import parse_json_block

WINDOW = (PAGE_T - 60, PAGE_T)
ERROR_LEVEL = 0.05
SLOW_RATIO = 2.0
LOAD_RATIO = 1.5
STATES = ("erroring", "slow", "overloaded", "healthy")
EDGE_TYPES = ("failure", "load", "unreachable")


def _mean(env, svc, key, t0, t1):
    return sum(env.at[(t, svc)][key] for t in range(t0, t1)) / (t1 - t0)


class Truth:
    """What the simulation says about the incident at page time."""

    def __init__(self, incident, env):
        self.env = env
        self.no_outage = incident.no_outage
        edges, origins = true_graph(incident)
        self.edges = set(edges)
        self.roots = set(origins) | {f.svc for f in incident.faults}
        # the service whose deploy started a job that is a root counts as a root too ("session-api's post-deploy job")
        self.roots |= {svc for _, svc, _, msg in incident.events
                       for f in incident.faults if f.change in LOAD_SOURCES and f.change in msg}
        self.links = {(f.svc, f.link) for f in incident.faults if f.link}
        self.onset = {n: failure_onset([r for r in _rows(env)], n) for n in env.topo}

    def state(self, svc):
        env = self.env
        err = _mean(env, svc, "err", *WINDOW)
        base_err = _mean(env, svc, "err", 0, 90)
        lat, base_lat = _mean(env, svc, "lat_ms", *WINDOW), _mean(env, svc, "lat_ms", 0, 90)
        rps, base_rps = _mean(env, svc, "rps", *WINDOW), _mean(env, svc, "rps", 0, 90)
        health = env.call("check_health", {"service": svc})[1]["status"]
        erroring = err > ERROR_LEVEL    # pre-existing errors still count: the claim is about what the service does now
        return {"erroring": erroring, "slow": lat > SLOW_RATIO * base_lat,
                "overloaded": health == "saturated" or rps > LOAD_RATIO * base_rps + 5,
                "healthy": health == "healthy" and not erroring}

    def edge(self, src, dst, kind):
        """Edges are true if the propagation graph has them, or if the physics at page time shows them directly."""
        if kind == "unreachable":
            return (src, dst) in self.links
        if src in LOAD_SOURCES:
            return kind == "load" and (src, dst) in self.edges
        calls = dst in self.env.topo[src].deps
        if kind == "failure":
            callee, caller = self.state(dst), self.state(src)
            return (dst, src) in self.edges or calls and (callee["erroring"] or callee["slow"]) and caller["erroring"]
        return (src, dst) in self.edges or calls and self.state(dst)["overloaded"]


def _rows(env):
    return [r for (t, n), r in sorted(env.at.items())]


def _shows_state(obs, tool, svc, state):
    """Does one observation show this state for svc?"""
    if obs.get("service") != svc:
        return False
    if tool == "check_health":
        return {"healthy": obs["status"] == "healthy", "overloaded": obs["status"] == "saturated",
                "erroring": obs["own_err"] > HEALTH_ERR}.get(state, False)
    if tool == "get_logs":
        return state == "erroring" and obs["count"] > 0 or state == "healthy" and obs["count"] == 0
    if tool == "get_metrics":
        s = obs["series"]
        first, last = s[0], s[-4:]
        err = sum(x["err"] for x in last) / len(last)
        return {"erroring": err > ERROR_LEVEL,
                "slow": max(x["lat_ms"] for x in last) > SLOW_RATIO * first["lat_ms"],
                "overloaded": max(x["rps"] for x in last) > LOAD_RATIO * first["rps"] + 5,
                "healthy": max(x["err"] for x in last) <= ERROR_LEVEL}.get(state, False)
    return False


def _touches(step, svc):
    a = step["args"] if isinstance(step["args"], dict) else {}
    return svc in (a.get("service"), a.get("src"), a.get("dst"))


def _supported(claim, cited):
    kind = claim.get("kind")
    if kind == "state":
        return any(_shows_state(s["observed"], s["tool"], claim["service"], claim["state"]) for s in cited)
    if kind == "edge":
        src, dst, typ = claim["src"], claim["dst"], claim["type"]
        if typ == "unreachable":
            return any(s["tool"] == "check_connectivity" and s["observed"].get("src") == src and s["observed"].get("dst") == dst
                       and (s["observed"].get("refused") or 0) > ERROR_LEVEL for s in cited) or any(_connect_log(s, src, dst) for s in cited)
        if typ == "failure":
            if any(_shows_state(s["observed"], s["tool"], dst, "healthy") and s["tool"] == "check_health" for s in cited):
                return False        # its own evidence says the callee is healthy
            return any(_shows_state(s["observed"], s["tool"], dst, st) for s in cited for st in ("erroring", "slow")) or any(
                s["tool"] == "get_logs" and s["observed"].get("service") == src and dst in (s["observed"].get("message") or "")
                for s in cited)
        return any(_shows_state(s["observed"], s["tool"], dst, "overloaded") for s in cited)
    if kind == "cause":
        svc = claim["service"]
        if svc not in Environment.KNOWN:      # "no single root cause": nothing specific to show
            return bool(cited)
        return any(_touches(s, svc) and not _shows_state(s["observed"], s["tool"], svc, "healthy") for s in cited) or any(
            s["tool"] == "get_changes" and any(e[1] == svc for e in s["observed"]["events"]) for s in cited)
    return False


def _connect_log(step, src, dst):
    """A log line from src that names dst and a connection failure also shows the link problem."""
    msg = (step["observed"].get("message") or "").lower() if step["tool"] == "get_logs" else ""
    return step["observed"].get("service") == src and dst in msg and ("refused" in msg or "connect" in msg)


def _grounded(claim, steps):
    if claim.get("kind") == "edge":
        if claim["type"] == "unreachable":
            return any(s["tool"] == "check_connectivity" and _touches(s, claim["src"]) and _touches(s, claim["dst"])
                       or _connect_log(s, claim["src"], claim["dst"]) for s in steps)
        return any(_touches(s, claim["dst"]) for s in steps)
    svc = claim.get("service")
    if claim.get("kind") == "cause" and svc not in Environment.KNOWN:
        return bool(steps)
    return any(_touches(s, svc) for s in steps) or claim.get("kind") == "cause" and any(
        s["tool"] == "get_changes" and any(e[1] == svc for e in s["observed"]["events"]) for s in steps)


def _true(claim, truth):
    kind = claim.get("kind")
    if kind == "state":
        return truth.state(claim["service"]).get(claim["state"], False)
    if kind == "edge":
        return truth.edge(claim["src"], claim["dst"], claim["type"])
    if truth.no_outage:     # nothing is broken: only "no root cause" is true
        return claim.get("service") not in truth.env.topo and claim.get("service") not in LOAD_SOURCES
    return claim.get("service") in truth.roots


def _valid(claim, env):
    kind = claim.get("kind")
    if kind == "state":
        return claim.get("service") in env.topo and claim.get("state") in STATES
    if kind == "edge":
        return (claim.get("src") in env.topo or claim.get("src") in LOAD_SOURCES) and claim.get("dst") in env.topo \
            and claim.get("type") in EDGE_TYPES
    return kind == "cause" and isinstance(claim.get("service"), str)


def describe(claim):
    if claim.get("kind") == "edge":
        return f"{claim.get('src')} -> {claim.get('dst')} ({claim.get('type')})"
    if claim.get("kind") == "state":
        return f"{claim.get('service')} is {claim.get('state')}"
    return f"root cause is {claim.get('service')}"


def missed_checks(incident, env, truth, steps):
    """True-path inspections the AI never made, each with the output it would have seen."""
    out = []
    for src, dst in sorted(truth.links):
        if not any(s["tool"] == "check_connectivity" and _touches(s, src) and _touches(s, dst) for s in steps):
            out.append({"tool": "check_connectivity", "args": {"src": src, "dst": dst},
                        "would_show": env.call("check_connectivity", {"src": src, "dst": dst})[0]})
    for svc in sorted(r for r in truth.roots if r in env.topo):
        if not any(s["tool"] == "check_health" and _touches(s, svc) for s in steps):
            out.append({"tool": "check_health", "args": {"service": svc},
                        "would_show": env.call("check_health", {"service": svc})[0]})
    for svc in sorted({n for e in truth.edges for n in e if n in env.topo} - truth.roots):
        if not any(_touches(s, svc) for s in steps):
            out.append({"tool": "get_metrics", "args": {"service": svc},
                        "would_show": env.call("get_metrics", {"service": svc})[0].splitlines()[-1]})
    return out


def verify_trace(incident, record):
    env = Environment(incident)
    truth = Truth(incident, env)
    steps = record["steps"]
    index = {s["id"]: i for i, s in enumerate(steps)}
    block = parse_json_block(record.get("answer") or "") or {}
    claims = block.get("claims") or []
    results = []
    for c in claims:
        if not isinstance(c, dict) or not _valid(c, env):
            results.append({"claim": c, "text": str(c), "label": "MALFORMED", "grounded": False, "supported": False,
                            "true": None, "evidence": [], "commit_step": None})
            continue
        cited = [steps[index[e]] for e in c.get("evidence", []) if e in index]
        g, sup, tr = _grounded(c, steps), _supported(c, cited), _true(c, truth)
        label = "FALSE" if not tr else "UNGROUNDED" if not g else "UNSUPPORTED" if not sup else "VERIFIED"
        commit = min((index[e] for e in c.get("evidence", []) if e in index), default=None)
        results.append({"claim": c, "text": describe(c), "label": label, "grounded": g, "supported": sup, "true": tr,
                        "evidence": [s["id"] for s in cited], "commit_step": None if commit is None else steps[commit]["id"]})
    missed = missed_checks(incident, env, truth, steps)
    bad = [r for r in results if r["label"] != "VERIFIED"]
    first_wrong = None
    if bad:
        key = lambda r: index.get(r["commit_step"], len(steps))
        worst = min(bad, key=key)
        catch = next((m for m in missed if worst["claim"].get("dst") in m["args"].values()
                      or worst["claim"].get("service") in m["args"].values()), None)
        first_wrong = {"step": worst["commit_step"] or "final answer", "claim": worst["text"], "label": worst["label"],
                       "would_have_caught_it": catch}
    counts = {k: sum(r["label"] == k for r in results) for k in ("VERIFIED", "UNSUPPORTED", "UNGROUNDED", "FALSE", "MALFORMED")}
    return {"task": record["task"], "run": record["run"], "n_steps": len(steps),
            "tools_used": sorted({s["tool"] for s in steps}), "claims": results, "counts": counts,
            "missed_checks": missed, "first_wrong": first_wrong,
            "clean": bool(results) and not bad}
