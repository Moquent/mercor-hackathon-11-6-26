"""Claim schema and the per-relationship verification logic.

Claims are evaluated independently: one invalid or false claim never affects its siblings.
"""
from __future__ import annotations

from typing import Any

import networkx as nx
from pydantic import BaseModel, ConfigDict

from app.verifier.evidence import CoverageMetadata, Verdict, edge_evidence, make_result, resolve_coverage
from app.verifier.reachability import get_ancestors, known_event, nearest_common_ancestors, verify_reachability

COMMIT_STATUS = "committed"
EVALUABLE = ("reachable", "not_reachable", "insufficient_evidence", "direct_dependency", "shared_ancestor",
             "no_shared_ancestor", "produced_commit", "not_produced_commit")


class Claim(BaseModel):
    """`relationship` is a free string so that an unevaluable one reaches the checker and earns INVALID_CLAIM."""

    model_config = ConfigDict(extra="ignore")
    source_event_id: str
    target_event_id: str
    relationship: str
    ancestor_event_id: str | None = None


def _claim_dict(claim: Claim) -> dict[str, Any]:
    out: dict[str, Any] = {"source": claim.source_event_id, "target": claim.target_event_id, "relationship": claim.relationship}
    if claim.ancestor_event_id:
        out["ancestor"] = claim.ancestor_event_id
    return out


def _reachable_result(claim: Claim, graph: nx.DiGraph, coverage: CoverageMetadata | dict | None) -> dict[str, Any]:
    return verify_reachability(claim.source_event_id, claim.target_event_id, graph, coverage)


def _not_reachable(claim: Claim, graph: nx.DiGraph, coverage) -> dict[str, Any]:
    base = _reachable_result(claim, graph, coverage)
    base["claim"] = _claim_dict(claim)
    flip = {
        Verdict.SUPPORTED.value: (Verdict.CONTRADICTED, "A recorded path exists, so the negative claim is false."),
        Verdict.CONTRADICTED.value: (Verdict.SUPPORTED, "No recorded path exists and the trace is closed-world complete, so non-reachability holds."),
    }
    if base["verdict"] in flip:
        verdict, why = flip[base["verdict"]]
        base["verdict"], base["explanation"] = verdict.value, why
    return base


def _insufficient(claim: Claim, graph: nx.DiGraph, coverage) -> dict[str, Any]:
    """The claim 'the evidence cannot decide'. True only with an open-world trace and no visible path."""
    base = _reachable_result(claim, graph, coverage)
    base["claim"] = _claim_dict(claim)
    verdict = base["verdict"]
    if verdict == Verdict.NOT_OBSERVED.value:
        base["verdict"] = Verdict.SUPPORTED.value
        base["explanation"] = "No path is visible and coverage is incomplete, so the evidence neither proves nor disproves the relationship."
    elif verdict == Verdict.SUPPORTED.value:
        base["verdict"] = Verdict.CONTRADICTED.value
        base["explanation"] = "The evidence is sufficient: a recorded path proves the relationship even though coverage may be partial."
    elif verdict == Verdict.CONTRADICTED.value:
        base["explanation"] = "The evidence is sufficient: the trace is closed-world complete and no path exists."
    return base


def _direct(claim: Claim, graph: nx.DiGraph, coverage) -> dict[str, Any]:
    base = _reachable_result(claim, graph, coverage)
    base["claim"] = _claim_dict(claim)
    if base["verdict"] == Verdict.INVALID_CLAIM.value:
        return base
    source, target = claim.source_event_id, claim.target_event_id
    if graph.has_edge(source, target):
        base.update(verdict=Verdict.SUPPORTED.value, dependency_path=[source, target],
                    evidence=edge_evidence(graph, [source, target]),
                    explanation="A single recorded edge links the two events.")
        return base
    resolved, _ = resolve_coverage(graph, coverage)
    if resolved.closed_world:
        transitive = bool(base["dependency_path"])
        base["verdict"] = Verdict.CONTRADICTED.value
        base["explanation"] = ("The dependency is transitive, not direct: the shortest recorded path has intermediate events."
                               if transitive else "No recorded edge or path links these events in a complete trace.")
    else:
        base["verdict"] = Verdict.NOT_OBSERVED.value
        base["explanation"] = "No direct edge is visible, but coverage is incomplete."
    return base


def _shared_ancestor(claim: Claim, graph: nx.DiGraph, coverage) -> dict[str, Any]:
    """Source and target both descend from a third event (named, or any if unnamed).

    Shared ancestry says nothing about dependence between source and target; both directions are
    reported alongside so that a consumer cannot conflate them.
    """
    resolved, warnings = resolve_coverage(graph, coverage)
    payload = _claim_dict(claim)
    x, y, z = claim.source_event_id, claim.target_event_id, claim.ancestor_event_id
    for label, node in (("source", x), ("target", y)) + ((("ancestor", z),) if z else ()):
        if not known_event(graph, node):
            return make_result(payload, Verdict.INVALID_CLAIM, f"{label} id {node!r} is not an event in this evidence.",
                               coverage=resolved, warnings=warnings)
    if x == y or z in (x, y):
        return make_result(payload, Verdict.INVALID_CLAIM, "A shared ancestor relates three distinct events (or two, if the ancestor is unnamed).",
                           coverage=resolved, warnings=warnings)
    nearest = nearest_common_ancestors(x, y, graph)
    shared = get_ancestors(x, graph) & get_ancestors(y, graph)
    extra = {"x_reaches_y": nx.has_path(graph, x, y), "y_reaches_x": nx.has_path(graph, y, x),
             "nearest_common_ancestors": nearest}
    chosen = z if z else (nearest[0] if nearest else None)
    if chosen in shared:
        p1, p2 = nx.shortest_path(graph, chosen, x), nx.shortest_path(graph, chosen, y)
        return make_result(payload, Verdict.SUPPORTED, f"{chosen} is recorded upstream of both events.",
                           observed_reachable=None, dependency_path=p1,
                           evidence=edge_evidence(graph, p1) + edge_evidence(graph, p2),
                           coverage=resolved, warnings=warnings, second_path=p2, **extra)
    if resolved.closed_world:
        why = (f"{z} is not an ancestor of both events in the complete trace." if z
               else "The two events have no shared ancestor in the complete trace.")
        return make_result(payload, Verdict.CONTRADICTED, why, coverage=resolved, warnings=warnings, **extra)
    return make_result(payload, Verdict.NOT_OBSERVED, "No shared ancestor is visible, but coverage is incomplete.",
                       coverage=resolved, warnings=warnings, **extra)


def _no_shared_ancestor(claim: Claim, graph: nx.DiGraph, coverage) -> dict[str, Any]:
    base = _shared_ancestor(claim, graph, coverage)
    if base["verdict"] == Verdict.SUPPORTED.value:
        base["verdict"] = Verdict.CONTRADICTED.value
        base["explanation"] = "A shared ancestor is recorded, so the negative claim is false."
    elif base["verdict"] == Verdict.CONTRADICTED.value:
        base["verdict"] = Verdict.SUPPORTED.value
    return base


def _produced_commit(claim: Claim, graph: nx.DiGraph, coverage, negated: bool) -> dict[str, Any]:
    """Did this delivery attempt itself produce this committed state change?

    Plain reachability cannot answer this: a failed attempt reaches the eventual commit through the
    retry edge. Production needs (1) the target to be a committed write, (2) the source and the
    commit to share a delivery attempt, and (3) a path that does not cross a retry edge.
    """
    resolved, warnings = resolve_coverage(graph, coverage)
    payload = _claim_dict(claim)
    src, tgt = claim.source_event_id, claim.target_event_id
    for label, node in (("source", src), ("target", tgt)):
        if not known_event(graph, node):
            return make_result(payload, Verdict.INVALID_CLAIM, f"{label} id {node!r} is not an event in this evidence.",
                               coverage=resolved, warnings=warnings)
    s_node, t_node = graph.nodes[src], graph.nodes[tgt]
    if not s_node.get("delivery_attempt_id"):
        return make_result(payload, Verdict.INVALID_CLAIM, "The source event does not belong to a delivery attempt.",
                           coverage=resolved, warnings=warnings)
    if not t_node.get("database_transaction_id"):
        return make_result(payload, Verdict.INVALID_CLAIM, "The target event is not a database transaction outcome.",
                           coverage=resolved, warnings=warnings)

    no_retry = nx.subgraph_view(graph, filter_edge=lambda u, v: any(
        d["type"] != "retry_dependency" for d in graph.edges[u, v]["dependencies"]))
    same_attempt = s_node["delivery_attempt_id"] == t_node.get("delivery_attempt_id")
    committed = t_node.get("status") == COMMIT_STATUS
    path = nx.shortest_path(no_retry, src, tgt) if nx.has_path(no_retry, src, tgt) else []
    any_path = nx.shortest_path(graph, src, tgt) if nx.has_path(graph, src, tgt) else []
    facts = {"same_delivery_attempt": same_attempt, "target_committed": committed,
             "path_without_retry_edges": path, "path_any": any_path}

    if not committed:
        fact, why = False, f"Target has status {t_node.get('status')!r}: a transaction that did not commit produced no state change."
    elif not same_attempt:
        fact = False
        why = "The commit belongs to a different delivery attempt." + (
            " The source reaches it only through a retry edge, which is ordering, not production." if any_path else "")
    elif path:
        fact, why = True, "The commit carries this delivery attempt and is reachable without crossing a retry edge."
    elif resolved.closed_world:
        fact, why = False, "Same attempt id, but no recorded path links the source to the commit."
    else:
        return make_result(payload, Verdict.NOT_OBSERVED, "Same attempt id, but the connecting path is not visible in this incomplete evidence.",
                           observed_reachable=bool(any_path), coverage=resolved, warnings=warnings, **facts)

    verdict = Verdict.SUPPORTED if fact != negated else Verdict.CONTRADICTED
    shown = path if fact else any_path
    return make_result(payload, verdict, why, observed_reachable=bool(any_path), dependency_path=shown,
                       evidence=edge_evidence(graph, shown), coverage=resolved, warnings=warnings, **facts)


def verify_claim(claim: Claim | dict[str, Any], graph: nx.DiGraph, coverage: CoverageMetadata | dict | None = None) -> dict[str, Any]:
    try:
        parsed = claim if isinstance(claim, Claim) else Claim.model_validate(claim)
    except Exception as exc:
        resolved, warnings = resolve_coverage(graph, coverage)
        return make_result(claim if isinstance(claim, dict) else {"raw": str(claim)}, Verdict.INVALID_CLAIM,
                           f"Malformed claim: {type(exc).__name__}", coverage=resolved, warnings=warnings)
    rel = parsed.relationship
    if rel == "reachable":
        result = _reachable_result(parsed, graph, coverage)
    elif rel == "not_reachable":
        result = _not_reachable(parsed, graph, coverage)
    elif rel == "insufficient_evidence":
        result = _insufficient(parsed, graph, coverage)
    elif rel == "direct_dependency":
        result = _direct(parsed, graph, coverage)
    elif rel == "shared_ancestor":
        result = _shared_ancestor(parsed, graph, coverage)
    elif rel == "no_shared_ancestor":
        result = _no_shared_ancestor(parsed, graph, coverage)
    elif rel in ("produced_commit", "not_produced_commit"):
        result = _produced_commit(parsed, graph, coverage, negated=rel == "not_produced_commit")
    else:
        resolved, warnings = resolve_coverage(graph, coverage)
        result = make_result(_claim_dict(parsed), Verdict.INVALID_CLAIM,
                             f"The checker cannot evaluate {rel!r}. Reachability evidence does not establish root cause or counterfactual causation; that needs an intervention.",
                             coverage=resolved, warnings=warnings)
    result["claim"] = _claim_dict(parsed)
    return result


def verify_claims(claims: list[Any], graph: nx.DiGraph, coverage: CoverageMetadata | dict | None = None) -> list[dict[str, Any]]:
    return [verify_claim(c, graph, coverage) for c in claims]
