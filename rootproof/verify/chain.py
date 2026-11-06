"""Causal-chain verification: the propagation graph the AI *claims* versus the one the simulation *produces*.

True graph (from the no-action run, starting at the injected fault):
  failure edge  callee -> caller   the callee degraded first and the caller's failures follow it
  load edge     caller -> callee   the caller's retries or fallbacks multiplied the callee's request rate (>1.5x)
  source edge   job -> service     a cron or cleanup job's extra load lands on that service
Claimed graph: Jev answers one yes/no question per candidate edge about the AI's answer.
"""
from dataclasses import dataclass, field

from ..clients import jev
from ..config import JEV_THRESHOLD, PAGE_T
from ..scenario.incidents import topology_for
from ..scenario.topology import ENTRY
from ..sim import run
from .claims import LOAD_SOURCES
from .extract import parse_json_block

FAILURE_WINDOW_S = 5
LOAD_RATIO = 1.5


@dataclass
class ChainScore:
    true_edges: list = field(default_factory=list)
    origins: list = field(default_factory=list)
    claimed_edges: list = field(default_factory=list)
    claimed_p: dict = field(default_factory=dict)
    correct_edges: list = field(default_factory=list)
    unsupported_edges: list = field(default_factory=list)
    impossible_edges: list = field(default_factory=list)
    precision: float = 0.0
    recall: float = 0.0


def candidate_edges(topo):
    edges = set()
    for parent, svc in topo.items():
        for child in svc.deps:
            edges.add((child, parent))
            edges.add((parent, child))
    for job, svc in LOAD_SOURCES.items():
        edges.add((job, svc))
    return sorted(edges)


def failure_onset(rows, svc):
    """First second the service's own error rate or latency departs from baseline (load alone does not count)."""
    err = {r["t"]: r["err"] for r in rows if r["svc"] == svc}
    lat = {r["t"]: r["lat_ms"] for r in rows if r["svc"] == svc}
    base_err = sum(err[t] for t in range(90)) / 90
    base_lat = sum(lat[t] for t in range(90)) / 90
    return next((t for t in range(90, PAGE_T) if err[t] > base_err + 0.05 or lat[t] > 2 * base_lat), None)


def true_graph(incident):
    """Returns (true edges, origins). No-outage incidents have no propagation."""
    if incident.no_outage:
        return set(), []
    topo = topology_for(incident)
    rows = run(topo, incident.faults, ENTRY)
    rps = {(r["t"], r["svc"]): r["rps"] for r in rows}
    onset = {n: failure_onset(rows, n) for n in topo}
    origins = [f.change if f.change in LOAD_SOURCES else f.svc for f in incident.faults]
    edges, frontier, seen = set(), list(origins), set(origins)
    while frontier:
        x = frontier.pop()
        nexts = []
        if x in LOAD_SOURCES:
            nexts.append(LOAD_SOURCES[x])
        else:
            for parent, svc in topo.items():
                if x in svc.deps and onset.get(parent) is not None and (onset.get(x) is None or onset[parent] >= onset[x] - FAILURE_WINDOW_S):
                    nexts.append(parent)
            amplified = topo[x].deps if topo[x].retries else topo[x].deps[1:] if topo[x].mode == "fallback" else []
            for c in amplified:
                if rps[(PAGE_T, c)] > LOAD_RATIO * max(1.0, rps[(60, c)]):
                    nexts.append(c)
        for y in nexts:
            edges.add((x, y))
            if y not in seen:
                seen.add(y)
                frontier.append(y)
    return edges, origins


def claimed_graph(incident, answer):
    topo = topology_for(incident)
    block = parse_json_block(answer)
    state = answer if block is None else (
        f"Root cause: {block.get('root_cause_service')}\nMechanism: {block.get('mechanism')}\n"
        + "\n".join(f"- {a}" for a in block.get("actions", [])))
    cands = candidate_edges(topo)
    questions = {f"e{i}": {"type": "noul", "instructions": f"Does this diagnosis say that a problem at {a} caused, propagated to, "
                           f"or overloaded {b} (a causal step {a} -> {b})?"} for i, (a, b) in enumerate(cands)}
    answers = jev(state, questions)["answers"]
    return {cands[int(k[1:])]: round(v["noul"], 3) for k, v in answers.items() if v["noul"] >= JEV_THRESHOLD}


def score(incident, answer):
    truth, origins = true_graph(incident)
    claimed = claimed_graph(incident, answer)
    topo = topology_for(incident)
    real_calls = {(c, p) for p, svc in topo.items() for c in svc.deps} | {(p, c) for p, svc in topo.items() for c in svc.deps}
    real_calls |= set(LOAD_SOURCES.items())
    hits = [e for e in claimed if e in truth]
    wrong = [e for e in claimed if e not in truth]
    return ChainScore(
        true_edges=sorted(truth), origins=origins, claimed_edges=sorted(claimed),
        claimed_p={f"{a}->{b}": p for (a, b), p in claimed.items()},
        correct_edges=sorted(hits), unsupported_edges=sorted(wrong),
        impossible_edges=sorted(e for e in claimed if e not in real_calls),
        precision=len(hits) / len(claimed) if claimed else (1.0 if not truth else 0.0),
        recall=len(hits) / len(truth) if truth else 1.0)
