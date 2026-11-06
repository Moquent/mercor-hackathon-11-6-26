"""Stdlib port of DAGScope's verifier (Sarvesh, origin/sarvesh commit 5310275: app/verifier/*).

A claim about two events is judged only against recorded dependency edges. Timestamps are display
data; they never create an edge. Absence of a path licenses a negative verdict only on a
closed-world trace, and any integrity failure revokes closed-world status.

NetworkX is replaced by a small DiGraph with insertion-ordered adjacency, and shortest paths use the
same bidirectional BFS as networkx.bidirectional_shortest_path, so tie-breaks between equal-length
paths match DAGScope's saved results exactly.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

SUPPORTED, CONTRADICTED, NOT_OBSERVED, INVALID_CLAIM = "SUPPORTED", "CONTRADICTED", "NOT_OBSERVED", "INVALID_CLAIM"
VERDICTS = (SUPPORTED, CONTRADICTED, NOT_OBSERVED, INVALID_CLAIM)
COMMIT_STATUS = "committed"
EVALUABLE = ("reachable", "not_reachable", "insufficient_evidence", "direct_dependency", "shared_ancestor",
             "no_shared_ancestor", "produced_commit", "not_produced_commit")


class Verdict:  # enum-like namespace, values are plain strings
    SUPPORTED, CONTRADICTED, NOT_OBSERVED, INVALID_CLAIM = VERDICTS


# ---------------------------------------------------------------- coverage
def coverage(closed_world=False, level="unknown", source="unspecified", notes=""):
    return {"closed_world": bool(closed_world), "level": level, "source": source, "notes": notes}


UNKNOWN_COVERAGE = coverage(False, "unknown", "default", "no coverage metadata supplied; treated as incomplete")


def _cov(raw):
    if raw is None:
        return dict(UNKNOWN_COVERAGE)
    if not isinstance(raw, dict):
        raise TypeError("coverage must be a dict")
    return coverage(raw.get("closed_world", False), raw.get("level", "unknown"),
                    raw.get("source", "unspecified"), raw.get("notes", ""))


# ---------------------------------------------------------------- graph
class DAG:
    """Directed graph: nodes {id: attrs}, succ/pred {id: {other: edge}}; edge = {"dependencies": [...]}."""

    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.succ: dict[str, dict[str, dict]] = {}
        self.pred: dict[str, dict[str, dict]] = {}
        self.meta: dict[str, Any] = {}

    def add_node(self, n, **attrs):
        if n not in self.nodes:
            self.nodes[n] = {}
            self.succ[n] = {}
            self.pred[n] = {}
        self.nodes[n].update(attrs)

    def add_edge(self, u, v, **attrs):
        for n in (u, v):
            if n not in self.nodes:
                self.add_node(n)
        if v not in self.succ[u]:
            d = dict(attrs)
            self.succ[u][v] = d
            self.pred[v][u] = d

    def __contains__(self, n):
        return isinstance(n, str) and n in self.nodes

    def has_edge(self, u, v):
        return u in self.succ and v in self.succ[u]

    def edge(self, u, v):
        return self.succ[u][v]

    def edges(self):
        return [(u, v) for u in self.succ for v in self.succ[u]]

    def number_of_edges(self):
        return sum(len(s) for s in self.succ.values())


def build_dependency_dag(events, dependencies, coverage_meta=None) -> DAG:
    """Parent -> child graph from dependency records only. Dangling parents become missing=True placeholders."""
    event_rows = [dict(e) for e in events]
    dep_rows = [dict(d) for d in dependencies]
    g = DAG()
    ids = Counter(e["event_id"] for e in event_rows)
    for e in sorted(event_rows, key=lambda e: e["event_id"]):
        g.add_node(e["event_id"], **e, missing=False)
    for d in sorted(dep_rows, key=lambda d: (d["parent_event_id"], d["child_event_id"], d["dependency_type"])):
        p, c = d["parent_event_id"], d["child_event_id"]
        for n in (p, c):
            if n not in g:
                g.add_node(n, event_id=n, missing=True)
        if not g.has_edge(p, c):
            g.add_edge(p, c, dependencies=[])
        g.edge(p, c)["dependencies"].append({"type": d["dependency_type"], "evidence_reference": d.get("evidence_reference", "")})
    g.meta["coverage"] = coverage_meta
    g.meta["duplicate_event_ids"] = sorted(k for k, n in ids.items() if n > 1)
    g.meta["structural_issues"] = structural_issues(g)
    return g


def find_cycles(g: DAG) -> list[list[str]]:
    """Strongly connected components with a cycle (iterative Tarjan), each sorted."""
    index, low, on, stack, out, counter = {}, {}, set(), [], [], [0]
    for root in g.nodes:
        if root in index:
            continue
        work = [(root, iter(g.succ[root]))]
        index[root] = low[root] = counter[0]; counter[0] += 1
        stack.append(root); on.add(root)
        while work:
            v, it = work[-1]
            nxt = next(it, None)
            if nxt is not None:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]; counter[0] += 1
                    stack.append(nxt); on.add(nxt)
                    work.append((nxt, iter(g.succ[nxt])))
                elif nxt in on:
                    low[v] = min(low[v], index[nxt])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop(); on.discard(w); comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1 or g.has_edge(v, v):
                    out.append(sorted(comp))
    return out


def is_dag(g: DAG) -> bool:
    return not find_cycles(g)


def structural_issues(g: DAG) -> list[str]:
    issues = []
    if g.meta.get("duplicate_event_ids"):
        issues.append(f"duplicate event ids: {g.meta['duplicate_event_ids']}")
    missing = sorted(n for n, d in g.nodes.items() if d.get("missing"))
    if missing:
        issues.append(f"dangling references: {missing}")
    if not is_dag(g):
        issues.append("dependency cycle detected")
    return issues


def validate_graph_integrity(g: DAG, events) -> dict[str, Any]:
    rows = [dict(e) for e in events]
    counts = Counter(e["event_id"] for e in rows)
    duplicates = sorted(k for k, n in counts.items() if n > 1)
    known = set(counts)
    dangling = sorted(({"parent": u, "child": v} for u, v in g.edges() if u not in known or v not in known),
                      key=lambda d: (d["parent"], d["child"]))
    cycles = find_cycles(g)
    mismatches, skew = [], []
    for e in rows:
        child = e["event_id"]
        declared = set(e.get("parent_event_ids") or [])
        recorded = set(g.pred[child]) if child in g else set()
        if declared != recorded:
            mismatches.append({"event_id": child, "only_in_event": sorted(declared - recorded),
                               "only_in_dependencies": sorted(recorded - declared)})
        for p in recorded:
            pts = g.nodes[p].get("timestamp")
            if pts and e.get("timestamp") and str(pts) > str(e["timestamp"]):
                skew.append({"parent": p, "child": child})
    return {"valid": not (duplicates or dangling or cycles or mismatches), "duplicate_event_ids": duplicates,
            "dangling_references": dangling, "cycles": cycles, "parent_list_mismatches": mismatches,
            "clock_skew_warnings": skew, "event_count": len(known), "edge_count": g.number_of_edges()}


# ---------------------------------------------------------------- traversal
def _edge_ok(g, u, v, edge_filter):
    return edge_filter is None or edge_filter(g, u, v)


def shortest_path(g: DAG, source, target, edge_filter=None):
    """networkx.bidirectional_shortest_path, same expansion order. Returns None if no path."""
    if source not in g or target not in g:
        return None
    if source == target:
        return [source]
    pred, succ = {source: None}, {target: None}
    ff, rf = [source], [target]
    meet = None
    while ff and rf and meet is None:
        if len(ff) <= len(rf):
            level, ff = ff, []
            for v in level:
                for w in g.succ[v]:
                    if not _edge_ok(g, v, w, edge_filter):
                        continue
                    if w not in pred:
                        ff.append(w)
                        pred[w] = v
                    if w in succ:
                        meet = w
                        break
                if meet is not None:
                    break
        else:
            level, rf = rf, []
            for v in level:
                for w in g.pred[v]:
                    if not _edge_ok(g, w, v, edge_filter):
                        continue
                    if w not in succ:
                        succ[w] = v
                        rf.append(w)
                    if w in pred:
                        meet = w
                        break
                if meet is not None:
                    break
    if meet is None:
        return None
    path, w = [], meet
    while w is not None:
        path.append(w)
        w = pred[w]
    path.reverse()
    w = succ[meet]
    while w is not None:
        path.append(w)
        w = succ[w]
    return path


def has_path(g, s, t, edge_filter=None):
    return shortest_path(g, s, t, edge_filter) is not None


def _walk(g, start, adj):
    seen, stack = set(), [start]
    while stack:
        for w in adj[stack.pop()]:
            if w not in seen:
                seen.add(w)
                stack.append(w)
    seen.discard(start)
    return seen


def get_ancestors(n, g):
    return _walk(g, n, g.pred) if n in g else set()


def get_descendants(n, g):
    return _walk(g, n, g.succ) if n in g else set()


def known_event(g, n):
    return isinstance(n, str) and n in g and not g.nodes[n].get("missing")


def nearest_common_ancestors(a, b, g):
    shared = get_ancestors(a, g) & get_ancestors(b, g)
    return sorted(n for n in shared if not (get_descendants(n, g) & shared))


# ---------------------------------------------------------------- evidence / results
def resolve_coverage(g: DAG, cov=None):
    """Explicit argument > graph attribute > open-world. Integrity failures revoke closed-world status."""
    raw = cov if cov is not None else g.meta.get("coverage")
    c = _cov(raw)
    warnings = []
    issues = g.meta.get("structural_issues", [])
    if issues:
        warnings = list(issues)
        if c["closed_world"]:
            c = {**c, "closed_world": False, "level": "partial",
                 "notes": "closed-world claim revoked: the trace failed integrity validation"}
    return c, warnings


def edge_evidence(g, path):
    out = []
    for p, c in zip(path, path[1:]):
        for d in g.edge(p, c)["dependencies"]:
            out.append({"parent": p, "child": c, "type": d["type"], "evidence_reference": d["evidence_reference"]})
    return out


def explain_dependency_path(source, target, g):
    path = shortest_path(g, source, target)
    if not path or source == target:
        return {"path": [], "hops": [], "text": f"no recorded dependency path from {source} to {target}"}
    names = [f"{g.nodes[n].get('event_type', '?')}({n[:8]})" for n in path]
    return {"path": path, "hops": edge_evidence(g, path), "text": " -> ".join(names)}


def make_result(claim, verdict, explanation, observed_reachable=None, dependency_path=None, evidence=None,
                coverage=None, warnings=None, **extra):
    return {"claim": claim, "verdict": verdict, "observed_reachable": observed_reachable,
            "dependency_path": dependency_path or [], "evidence": evidence or [], "explanation": explanation,
            "coverage": coverage, "integrity_warnings": warnings or [],
            "evidence_class": "recorded_dependency_reachability", **extra}


def verify_reachability(src, tgt, g, cov=None):
    claim = {"source": src, "target": tgt, "relationship": "reachable"}
    c, w = resolve_coverage(g, cov)
    for label, node in (("source", src), ("target", tgt)):
        if not known_event(g, node):
            return make_result(claim, INVALID_CLAIM, f"{label} id {node!r} is not an event in this evidence.", coverage=c, warnings=w)
    if src == tgt:
        return make_result(claim, INVALID_CLAIM, "source and target are the same event; reachability between distinct events is required.",
                           coverage=c, warnings=w)
    path = shortest_path(g, src, tgt)
    if path:
        return make_result(claim, SUPPORTED, f"The recorded dependency path connects {src} to {tgt}.", observed_reachable=True,
                           dependency_path=path, evidence=edge_evidence(g, path), coverage=c, warnings=w)
    if c["closed_world"]:
        return make_result(claim, CONTRADICTED, "No recorded path exists and the trace is closed-world complete, so the claim is false for this execution.",
                           observed_reachable=False, coverage=c, warnings=w)
    return make_result(claim, NOT_OBSERVED, "No path is visible, but coverage is incomplete: absence of an edge here does not prove disconnection.",
                       observed_reachable=False, coverage=c, warnings=w)


# ---------------------------------------------------------------- claims
class MalformedClaim(ValueError):
    pass


def parse_claim(raw) -> dict:
    """Mirror of the pydantic Claim: three required strings, optional ancestor; extras ignored."""
    if not isinstance(raw, dict):
        raise MalformedClaim("claim is not an object")
    out = {}
    for k in ("source_event_id", "target_event_id", "relationship"):
        v = raw.get(k)
        if not isinstance(v, str):
            raise MalformedClaim(f"{k} missing or not a string")
        out[k] = v
    anc = raw.get("ancestor_event_id")
    if anc is not None and not isinstance(anc, str):
        raise MalformedClaim("ancestor_event_id not a string")
    out["ancestor_event_id"] = anc
    return out


def _claim_dict(c):
    out = {"source": c["source_event_id"], "target": c["target_event_id"], "relationship": c["relationship"]}
    if c.get("ancestor_event_id"):
        out["ancestor"] = c["ancestor_event_id"]
    return out


def _not_reachable(c, g, cov):
    base = verify_reachability(c["source_event_id"], c["target_event_id"], g, cov)
    flip = {SUPPORTED: (CONTRADICTED, "A recorded path exists, so the negative claim is false."),
            CONTRADICTED: (SUPPORTED, "No recorded path exists and the trace is closed-world complete, so non-reachability holds.")}
    if base["verdict"] in flip:
        base["verdict"], base["explanation"] = flip[base["verdict"]]
    return base


def _insufficient(c, g, cov):
    base = verify_reachability(c["source_event_id"], c["target_event_id"], g, cov)
    v = base["verdict"]
    if v == NOT_OBSERVED:
        base["verdict"] = SUPPORTED
        base["explanation"] = "No path is visible and coverage is incomplete, so the evidence neither proves nor disproves the relationship."
    elif v == SUPPORTED:
        base["verdict"] = CONTRADICTED
        base["explanation"] = "The evidence is sufficient: a recorded path proves the relationship even though coverage may be partial."
    elif v == CONTRADICTED:
        base["explanation"] = "The evidence is sufficient: the trace is closed-world complete and no path exists."
    return base


def _direct(c, g, cov):
    base = verify_reachability(c["source_event_id"], c["target_event_id"], g, cov)
    if base["verdict"] == INVALID_CLAIM:
        return base
    s, t = c["source_event_id"], c["target_event_id"]
    if g.has_edge(s, t):
        base.update(verdict=SUPPORTED, dependency_path=[s, t], evidence=edge_evidence(g, [s, t]),
                    explanation="A single recorded edge links the two events.")
        return base
    resolved, _ = resolve_coverage(g, cov)
    if resolved["closed_world"]:
        base["verdict"] = CONTRADICTED
        base["explanation"] = ("The dependency is transitive, not direct: the shortest recorded path has intermediate events."
                               if base["dependency_path"] else "No recorded edge or path links these events in a complete trace.")
    else:
        base["verdict"] = NOT_OBSERVED
        base["explanation"] = "No direct edge is visible, but coverage is incomplete."
    return base


def _shared_ancestor(c, g, cov):
    resolved, w = resolve_coverage(g, cov)
    payload = _claim_dict(c)
    x, y, z = c["source_event_id"], c["target_event_id"], c.get("ancestor_event_id")
    for label, node in (("source", x), ("target", y)) + ((("ancestor", z),) if z else ()):
        if not known_event(g, node):
            return make_result(payload, INVALID_CLAIM, f"{label} id {node!r} is not an event in this evidence.", coverage=resolved, warnings=w)
    if x == y or z in (x, y):
        return make_result(payload, INVALID_CLAIM, "A shared ancestor relates three distinct events (or two, if the ancestor is unnamed).",
                           coverage=resolved, warnings=w)
    nearest = nearest_common_ancestors(x, y, g)
    shared = get_ancestors(x, g) & get_ancestors(y, g)
    extra = {"x_reaches_y": has_path(g, x, y), "y_reaches_x": has_path(g, y, x), "nearest_common_ancestors": nearest}
    chosen = z if z else (nearest[0] if nearest else None)
    if chosen in shared:
        p1, p2 = shortest_path(g, chosen, x), shortest_path(g, chosen, y)
        return make_result(payload, SUPPORTED, f"{chosen} is recorded upstream of both events.", observed_reachable=None,
                           dependency_path=p1, evidence=edge_evidence(g, p1) + edge_evidence(g, p2),
                           coverage=resolved, warnings=w, second_path=p2, **extra)
    if resolved["closed_world"]:
        why = (f"{z} is not an ancestor of both events in the complete trace." if z
               else "The two events have no shared ancestor in the complete trace.")
        return make_result(payload, CONTRADICTED, why, coverage=resolved, warnings=w, **extra)
    return make_result(payload, NOT_OBSERVED, "No shared ancestor is visible, but coverage is incomplete.", coverage=resolved, warnings=w, **extra)


def _no_shared_ancestor(c, g, cov):
    base = _shared_ancestor(c, g, cov)
    if base["verdict"] == SUPPORTED:
        base["verdict"] = CONTRADICTED
        base["explanation"] = "A shared ancestor is recorded, so the negative claim is false."
    elif base["verdict"] == CONTRADICTED:
        base["verdict"] = SUPPORTED
    return base


def _no_retry(g, u, v):
    return any(d["type"] != "retry_dependency" for d in g.edge(u, v)["dependencies"])


def _produced_commit(c, g, cov, negated):
    """Production needs: target committed, same delivery attempt, and a path that crosses no retry edge."""
    resolved, w = resolve_coverage(g, cov)
    payload = _claim_dict(c)
    src, tgt = c["source_event_id"], c["target_event_id"]
    for label, node in (("source", src), ("target", tgt)):
        if not known_event(g, node):
            return make_result(payload, INVALID_CLAIM, f"{label} id {node!r} is not an event in this evidence.", coverage=resolved, warnings=w)
    s, t = g.nodes[src], g.nodes[tgt]
    if not s.get("delivery_attempt_id"):
        return make_result(payload, INVALID_CLAIM, "The source event does not belong to a delivery attempt.", coverage=resolved, warnings=w)
    if not t.get("database_transaction_id"):
        return make_result(payload, INVALID_CLAIM, "The target event is not a database transaction outcome.", coverage=resolved, warnings=w)
    same = s["delivery_attempt_id"] == t.get("delivery_attempt_id")
    committed = t.get("status") == COMMIT_STATUS
    path = (shortest_path(g, src, tgt, _no_retry) or []) if src != tgt else [src]
    any_path = (shortest_path(g, src, tgt) or []) if src != tgt else [src]
    facts = {"same_delivery_attempt": same, "target_committed": committed, "path_without_retry_edges": path, "path_any": any_path}
    if not committed:
        fact, why = False, f"Target has status {t.get('status')!r}: a transaction that did not commit produced no state change."
    elif not same:
        fact = False
        why = "The commit belongs to a different delivery attempt." + (
            " The source reaches it only through a retry edge, which is ordering, not production." if any_path else "")
    elif path:
        fact, why = True, "The commit carries this delivery attempt and is reachable without crossing a retry edge."
    elif resolved["closed_world"]:
        fact, why = False, "Same attempt id, but no recorded path links the source to the commit."
    else:
        return make_result(payload, NOT_OBSERVED, "Same attempt id, but the connecting path is not visible in this incomplete evidence.",
                           observed_reachable=bool(any_path), coverage=resolved, warnings=w, **facts)
    verdict = SUPPORTED if fact != negated else CONTRADICTED
    shown = path if fact else any_path
    return make_result(payload, verdict, why, observed_reachable=bool(any_path), dependency_path=shown,
                       evidence=edge_evidence(g, shown), coverage=resolved, warnings=w, **facts)


def verify_claim(claim, g: DAG, cov=None) -> dict:
    try:
        c = parse_claim(claim)
    except MalformedClaim as exc:
        resolved, w = resolve_coverage(g, cov)
        return make_result(claim if isinstance(claim, dict) else {"raw": str(claim)}, INVALID_CLAIM,
                           f"Malformed claim: {exc}", coverage=resolved, warnings=w)
    rel = c["relationship"]
    if rel == "reachable":
        r = verify_reachability(c["source_event_id"], c["target_event_id"], g, cov)
    elif rel == "not_reachable":
        r = _not_reachable(c, g, cov)
    elif rel == "insufficient_evidence":
        r = _insufficient(c, g, cov)
    elif rel == "direct_dependency":
        r = _direct(c, g, cov)
    elif rel == "shared_ancestor":
        r = _shared_ancestor(c, g, cov)
    elif rel == "no_shared_ancestor":
        r = _no_shared_ancestor(c, g, cov)
    elif rel in ("produced_commit", "not_produced_commit"):
        r = _produced_commit(c, g, cov, negated=rel == "not_produced_commit")
    else:
        resolved, w = resolve_coverage(g, cov)
        r = make_result(_claim_dict(c), INVALID_CLAIM,
                        f"The checker cannot evaluate {rel!r}. Reachability evidence does not establish root cause or counterfactual causation; that needs an intervention.",
                        coverage=resolved, warnings=w)
    r["claim"] = _claim_dict(c)
    return r


def verify_claims(claims, g, cov=None):
    return [verify_claim(c, g, cov) for c in claims]


def verify_fault_origin(claimed_service, injected_service, candidates):
    claim = {"relationship": "fault_injected_in", "service": claimed_service}
    base = {"claim": claim, "evidence_class": "intervention_label", "injected_service": injected_service}
    if not isinstance(claimed_service, str) or claimed_service not in candidates:
        return {**base, "verdict": INVALID_CLAIM, "explanation": f"{claimed_service!r} is not one of the listed services."}
    if claimed_service == injected_service:
        return {**base, "verdict": SUPPORTED, "explanation": f"The experiment injected the fault into {injected_service}."}
    return {**base, "verdict": CONTRADICTED, "explanation": f"The experiment injected the fault into {injected_service}, not {claimed_service}."}


# ---------------------------------------------------------------- tasks: mask, view, grade (app/scenarios + app/evaluation)
ERROR_CATEGORY = {"scenario_1": "temporal_correlation", "scenario_2": "retry_attribution",
                  "scenario_3": "partial_observability", "scenario_4": "shared_ancestor",
                  "rcaeval_reachability": "real_trace_reachability"}


def apply_mask(artifact, mask):
    """The AI-visible subset of a run: (events, dependencies, hidden_event_ids)."""
    if not mask:
        return artifact["events"], artifact["dependencies"], []
    names = artifact["manifest"]
    hidden = {names[n] for n in mask.get("hide_events", [])}
    hidden_edges = {(names[a], names[b]) for a, b in mask.get("hide_edges", [])}
    events, deps = [], []
    for e in artifact["events"]:
        if e["event_id"] in hidden:
            continue
        parents = [p for p in e["parent_event_ids"] if (p, e["event_id"]) not in hidden_edges]
        if mask.get("scrub_dangling_refs", True):
            parents = [p for p in parents if p not in hidden]
        events.append({**e, "parent_event_ids": parents})
    for d in artifact["dependencies"]:
        pair = (d["parent_event_id"], d["child_event_id"])
        if pair in hidden_edges or pair[0] in hidden or pair[1] in hidden:
            continue
        deps.append(d)
    return events, deps, sorted(hidden)


def hidden_edges(artifact, mask):
    """Dependency records removed by the mask (for display: edges the AI never saw)."""
    _, deps, _ = apply_mask(artifact, mask)
    kept = {(d["parent_event_id"], d["child_event_id"], d["dependency_type"]) for d in deps}
    return [d for d in artifact["dependencies"] if (d["parent_event_id"], d["child_event_id"], d["dependency_type"]) not in kept]


def task_view(artifact, truth):
    """The graph a claim is judged against: exactly what the AI was shown, with its coverage."""
    mask = truth["ground_truth"]["mask"]
    events, deps, _ = apply_mask(artifact, mask)
    if mask:
        cov = coverage(False, "partial", "AI-visible export with hidden events/links",
                       "Incomplete by construction; the private graph is kept for ground truth only.")
    else:
        cov = _cov(artifact["coverage"])
    return build_dependency_dag(events, deps, cov), cov


def grade(attempt, public_task, truth, artifact, graph_cov=None):
    slots = truth["ground_truth"]["slots"]
    base = {"task_id": truth["task_id"], "scenario_id": truth["scenario_id"], "coverage": truth["coverage"],
            "category": ERROR_CATEGORY.get(truth["scenario_id"], truth["scenario_id"]), "source": attempt.get("source", "model")}
    if attempt.get("parsed") is None:
        return {**base, "format_failure": True, "parse_error": attempt.get("parse_error"), "task_correct": False,
                "claims": [], "slot_results": [], "mismatch": f"Unparseable output: {attempt.get('parse_error')}"}
    graph, cov = graph_cov or task_view(artifact, truth)
    full_pairs = {tuple(p) for p in artifact["sql_closure"]}
    claims = []
    for raw in attempt["parsed"]["claims"]:
        result = verify_claim(raw, graph, cov)
        pair = (raw.get("source_event_id"), raw.get("target_event_id"))
        claims.append({"raw": raw, "result": result, "reachable_in_private_graph": pair in full_pairs})
    slot_results, used = [], set()
    for slot in slots:
        match = next((i for i, c in enumerate(claims) if i not in used
                      and c["raw"].get("source_event_id") == slot["source"] and c["raw"].get("target_event_id") == slot["target"]
                      and c["raw"].get("relationship") in slot["allowed"]), None)
        if match is None:
            slot_results.append({"slot_index": slot["slot_index"], "status": "missing", "expected": slot["relationship"], "claimed": None})
            continue
        used.add(match)
        c = claims[match]
        ok = c["raw"]["relationship"] == slot["relationship"] and c["result"]["verdict"] == SUPPORTED
        if ok and slot["relationship"] == "shared_ancestor":
            ok = c["raw"].get("ancestor_event_id") in slot["acceptable_ancestors"]
        c["slot_index"] = slot["slot_index"]
        slot_results.append({"slot_index": slot["slot_index"], "status": "correct" if ok else "wrong", "expected": slot["relationship"],
                             "claimed": c["raw"]["relationship"], "verdict": c["result"]["verdict"],
                             "source_name": slot["source_name"], "target_name": slot["target_name"]})
    extras_rejected = [c for i, c in enumerate(claims) if i not in used and c["result"]["verdict"] != SUPPORTED]
    correct = all(s["status"] == "correct" for s in slot_results) and not extras_rejected
    wrong = [s for s in slot_results if s["status"] != "correct"]
    mismatch = "" if correct else "; ".join(
        f"{s.get('source_name', '?')} -> {s.get('target_name', '?')}: claimed {s['claimed']}, evidence supports {s['expected']}"
        + (f" (verdict {s['verdict']})" if s.get("verdict") else "") for s in wrong) or "Extra claim rejected by the checker."
    return {**base, "format_failure": False, "task_correct": correct, "claims": claims, "slot_results": slot_results,
            "confidence": attempt["parsed"].get("confidence"), "mismatch": mismatch}


def verdict_counts(graded):
    claims = [c for g in graded for c in g["claims"]]
    return {v: sum(c["result"]["verdict"] == v for c in claims) for v in VERDICTS}


# ---------------------------------------------------------------- prompt + answer parsing (app/evaluation)
CONDITIONS = {
    "explicit_parents": "Each log line lists parents=<event ids>: those are the recorded dependency edges (parent -> this event).",
    "chronological_only": "This export omits the dependency-edge column. Dependency edges were recorded, but you must judge them from the remaining log fields.",
}
COVERAGE_TEXT = {
    "complete": "COMPLETE. This is a closed-world export: every instrumented event and every recorded dependency of this execution is included.",
    "partial": "PARTIAL. The export came from a sampling collector: some events or dependency links of this execution may be missing from it.",
}
DEFINITIONS = """Relationship definitions (the source and target are event ids; edges point parent -> dependent child):
- reachable: this export establishes a directed path of recorded dependencies from source to target.
- direct_dependency: this export establishes a single recorded edge from source to target.
- not_reachable: this export establishes that no such path exists in the execution.
- insufficient_evidence: this export establishes neither reachable nor not_reachable.
- produced_commit: the source event belongs to the delivery attempt that itself performed the committed database transaction recorded by the target event.
- not_produced_commit: the source event's delivery attempt did not perform that committed transaction.
- shared_ancestor: a third, earlier event is recorded upstream of both source and target; give its id in ancestor_event_id.
- no_shared_ancestor: no event is recorded upstream of both source and target."""


def build_prompt(task, condition="explicit_parents"):
    logs = task["ai_visible_logs"] if condition == "explicit_parents" else task["ai_visible_logs_no_parents"]
    pairs = []
    for i, pair in enumerate(task["pairs"], 1):
        line = (f"{i}. source_event_id={pair['source_event_id']} target_event_id={pair['target_event_id']} "
                f"relationship: one of {', '.join(pair['allowed_relationships'])}")
        if pair["ancestor_required"]:
            line += " (include ancestor_event_id if you choose shared_ancestor)"
        pairs.append(line)
    return f"""You are helping investigate an incident in an asynchronous distributed order-processing system (API gateway, order service, inventory service, event worker; PostgreSQL and Redis Streams).

Question: {task['question']}

Coverage of this log export: {COVERAGE_TEXT[task['coverage']]}
{CONDITIONS[condition]}

{DEFINITIONS}

Log export:
```
{logs}
```

Report exactly one claim per listed item:
{chr(10).join(pairs)}

Respond with only a JSON object of this form:
{{"claims": [{{"source_event_id": "...", "target_event_id": "...", "relationship": "...", "ancestor_event_id": null}}], "explanation": "...", "confidence": 0.0}}
confidence is your overall confidence between 0 and 1."""


def parse_answer(raw):
    """Strict JSON; the only tolerance is one markdown fence around the whole response. Returns (parsed|None, error|None)."""
    import re
    if not raw or not raw.strip():
        return None, "empty response"
    m = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", raw, re.DOTALL)
    text = m.group(1) if m else raw.strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(obj, dict):
        return None, "schema violation: top level is not an object"
    claims = obj.get("claims")
    if not isinstance(claims, list):
        return None, "schema violation: ('claims',) missing or not a list"
    try:
        parsed_claims = [parse_claim(c) for c in claims]
    except MalformedClaim as exc:
        return None, f"schema violation: claims {exc}"
    if not isinstance(obj.get("explanation"), str):
        return None, "schema violation: ('explanation',) missing or not a string"
    conf = obj.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0.0 <= conf <= 1.0:
        return None, "schema violation: ('confidence',) must be a number in [0, 1]"
    return {"claims": parsed_claims, "explanation": obj["explanation"], "confidence": float(conf)}, None
