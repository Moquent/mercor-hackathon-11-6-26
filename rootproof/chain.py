"""Causal-chain verification: compare the propagation graph the AI *claims* with the one the simulation *actually* produced.

True graph (from the no-action simulation, starting at the injected fault):
  failure edge  callee -> caller   the callee degraded first and the caller's failures follow it
  load edge     caller -> callee   the caller's retries/fallbacks multiplied the callee's request rate (>1.5x)
  source edge   job -> service     a cron/cleanup job's extra load landed on that service
Claimed graph: Jev answers one yes/no question per candidate edge about the AI's prose.
"""
from incidents import ENTRY, topo_for
from llm import jev
from sim import PAGE_T, run
from verify import parse_json_block

JOBS = {"sub-poller": "docstore", "msg-cleanup": "notify-relay"}


def candidates(topo):
    edges = set()
    for p, s in topo.items():
        for c in s.deps:
            edges.add((c, p))
            edges.add((p, c))
    for j, svc in JOBS.items():
        edges.add((j, svc))
    return sorted(edges)


def fail_onset(rows, svc):
    """First second the service's own error rate or latency departs from baseline (load alone does not count)."""
    err = {r["t"]: r["err"] for r in rows if r["svc"] == svc}
    lat = {r["t"]: r["lat_ms"] for r in rows if r["svc"] == svc}
    b_err = sum(err[t] for t in range(90)) / 90
    b_lat = sum(lat[t] for t in range(90)) / 90
    return next((t for t in range(90, PAGE_T) if err[t] > b_err + 0.05 or lat[t] > 2 * b_lat), None)


def true_graph(inc):
    topo = topo_for(inc)
    if inc.get("no_outage"):
        return set(), []
    rows = run(topo, inc["faults"], ENTRY)
    rps = {(r["t"], r["svc"]): r["rps"] for r in rows}
    on = {n: fail_onset(rows, n) for n in topo}
    origins = []
    for f in inc["faults"]:
        origins.append(f.change if f.change in JOBS else f.svc)
    edges, frontier, seen = set(), list(origins), set(origins)
    while frontier:
        x = frontier.pop()
        nexts = []
        if x in JOBS:
            nexts.append(JOBS[x])
        else:
            for p, s in topo.items():
                if x in s.deps and on.get(p) is not None and (on.get(x) is None or on[p] >= on[x] - 5):
                    nexts.append(p)
            amplified = topo[x].deps if topo[x].retries else topo[x].deps[1:] if topo[x].mode == "fallback" else []
            for c in amplified:
                if rps[(PAGE_T, c)] > 1.5 * max(1.0, rps[(60, c)]):
                    nexts.append(c)
        for y in nexts:
            edges.add((x, y))
            if y not in seen:
                seen.add(y)
                frontier.append(y)
    return edges, origins


def claimed_graph(inc, answer):
    topo = topo_for(inc)
    blk = parse_json_block(answer)
    state = answer if blk is None else f"Root cause: {blk.get('root_cause_service')}\nMechanism: {blk.get('mechanism')}\n" + \
        "\n".join(f"- {a}" for a in blk.get("actions", []))
    cands = candidates(topo)
    qs = {f"e{i}": {"type": "noul", "instructions": f"Does this diagnosis say that a problem at {a} caused, propagated to, or "
                    f"overloaded {b} (a causal step {a} -> {b})?"} for i, (a, b) in enumerate(cands)}
    res = jev(state, qs)["answers"]
    return {cands[int(k[1:])]: round(v["noul"], 3) for k, v in res.items() if v["noul"] >= 0.5}


def score(inc, answer):
    truth, origins = true_graph(inc)
    claimed = claimed_graph(inc, answer)
    topo = topo_for(inc)
    real_calls = {(c, p) for p, s in topo.items() for c in s.deps} | {(p, c) for p, s in topo.items() for c in s.deps}
    real_calls |= set(JOBS.items())
    hits = [e for e in claimed if e in truth]
    wrong = [e for e in claimed if e not in truth]
    return {"true_edges": sorted(truth), "origins": origins, "claimed_edges": sorted(claimed), "claimed_p": {f"{a}->{b}": p for (a, b), p in claimed.items()},
            "correct_edges": sorted(hits), "unsupported_edges": sorted(wrong),
            "impossible_edges": sorted(e for e in claimed if e not in real_calls),
            "precision": len(hits) / len(claimed) if claimed else (1.0 if not truth else 0.0),
            "recall": len(hits) / len(truth) if truth else 1.0}
