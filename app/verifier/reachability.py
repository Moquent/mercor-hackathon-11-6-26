"""Core reachability engine. A path proves recorded dependency reachability -- not causation."""
from __future__ import annotations

from typing import Any

import networkx as nx

from app.verifier.evidence import CoverageMetadata, Verdict, edge_evidence, make_result, resolve_coverage


def known_event(graph: nx.DiGraph, event_id: Any) -> bool:
    return isinstance(event_id, str) and event_id in graph and not graph.nodes[event_id].get("missing")


def get_ancestors(event_id: str, graph: nx.DiGraph) -> set[str]:
    return set(nx.ancestors(graph, event_id)) if event_id in graph else set()


def get_descendants(event_id: str, graph: nx.DiGraph) -> set[str]:
    return set(nx.descendants(graph, event_id)) if event_id in graph else set()


def explain_dependency_path(source: str, target: str, graph: nx.DiGraph) -> dict[str, Any]:
    """Shortest recorded path with per-hop evidence and a readable rendering."""
    if source not in graph or target not in graph or not nx.has_path(graph, source, target):
        return {"path": [], "hops": [], "text": f"no recorded dependency path from {source} to {target}"}
    path = nx.shortest_path(graph, source, target)
    hops = edge_evidence(graph, path)
    names = [f"{graph.nodes[n].get('event_type', '?')}({n[:8]})" for n in path]
    return {"path": path, "hops": hops, "text": " -> ".join(names)}


def nearest_common_ancestors(a: str, b: str, graph: nx.DiGraph) -> list[str]:
    """Shared ancestors with no other shared ancestor below them."""
    shared = get_ancestors(a, graph) & get_ancestors(b, graph)
    return sorted(n for n in shared if not (get_descendants(n, graph) & shared))


def verify_reachability(
    source_event_id: str,
    target_event_id: str,
    graph: nx.DiGraph,
    coverage_metadata: CoverageMetadata | dict | None = None,
) -> dict[str, Any]:
    """Evaluate the claim "source reaches target"."""
    claim = {"source": source_event_id, "target": target_event_id, "relationship": "reachable"}
    coverage, warnings = resolve_coverage(graph, coverage_metadata)
    for label, node in (("source", source_event_id), ("target", target_event_id)):
        if not known_event(graph, node):
            return make_result(claim, Verdict.INVALID_CLAIM, f"{label} id {node!r} is not an event in this evidence.",
                               coverage=coverage, warnings=warnings)
    if source_event_id == target_event_id:
        return make_result(claim, Verdict.INVALID_CLAIM, "source and target are the same event; reachability between distinct events is required.",
                           coverage=coverage, warnings=warnings)
    if nx.has_path(graph, source_event_id, target_event_id):
        path = nx.shortest_path(graph, source_event_id, target_event_id)
        return make_result(claim, Verdict.SUPPORTED, f"The recorded dependency path connects {source_event_id} to {target_event_id}.",
                           observed_reachable=True, dependency_path=path, evidence=edge_evidence(graph, path),
                           coverage=coverage, warnings=warnings)
    if coverage.closed_world:
        return make_result(claim, Verdict.CONTRADICTED, "No recorded path exists and the trace is closed-world complete, so the claim is false for this execution.",
                           observed_reachable=False, coverage=coverage, warnings=warnings)
    return make_result(claim, Verdict.NOT_OBSERVED, "No path is visible, but coverage is incomplete: absence of an edge here does not prove disconnection.",
                       observed_reachable=False, coverage=coverage, warnings=warnings)
