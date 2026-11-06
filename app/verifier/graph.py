"""DAG construction and integrity validation.

Timestamps are carried as node data for display only. Nothing in this module reads them to create
an edge -- edges come exclusively from explicit dependency records.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

import networkx as nx

from app.verifier.evidence import CoverageMetadata


def _as_dict(row: Any) -> dict[str, Any]:
    return dict(row) if isinstance(row, Mapping) else row.model_dump()


def build_dependency_dag(
    events: Iterable[Any],
    dependencies: Iterable[Any],
    coverage: CoverageMetadata | dict | None = None,
) -> nx.DiGraph:
    """Build parent -> child graph. Dangling parents become `missing=True` placeholder nodes so that
    validation can report them; they are never valid claim endpoints."""
    event_rows = [_as_dict(e) for e in events]
    dep_rows = [_as_dict(d) for d in dependencies]
    graph = nx.DiGraph()
    ids = Counter(e["event_id"] for e in event_rows)
    # Sorted insertion makes tie-breaks between equal-length paths deterministic across runs.
    for event in sorted(event_rows, key=lambda e: e["event_id"]):
        graph.add_node(event["event_id"], **event, missing=False)
    for dep in sorted(dep_rows, key=lambda d: (d["parent_event_id"], d["child_event_id"], d["dependency_type"])):
        parent, child = dep["parent_event_id"], dep["child_event_id"]
        for node in (parent, child):
            if node not in graph:
                graph.add_node(node, event_id=node, missing=True)
        if not graph.has_edge(parent, child):
            graph.add_edge(parent, child, dependencies=[])
        graph.edges[parent, child]["dependencies"].append(
            {"type": dep["dependency_type"], "evidence_reference": dep.get("evidence_reference", "")}
        )
    graph.graph["coverage"] = coverage
    graph.graph["duplicate_event_ids"] = sorted(k for k, n in ids.items() if n > 1)
    graph.graph["structural_issues"] = structural_issues(graph)
    return graph


def structural_issues(graph: nx.DiGraph) -> list[str]:
    issues: list[str] = []
    if graph.graph.get("duplicate_event_ids"):
        issues.append(f"duplicate event ids: {graph.graph['duplicate_event_ids']}")
    missing = sorted(n for n, data in graph.nodes(data=True) if data.get("missing"))
    if missing:
        issues.append(f"dangling references: {missing}")
    if not nx.is_directed_acyclic_graph(graph):
        issues.append("dependency cycle detected")
    return issues


def validate_graph_integrity(graph: nx.DiGraph, events: Iterable[Any]) -> dict[str, Any]:
    """Report instrumentation errors. Clock skew is a warning: time never defines dependency."""
    rows = [_as_dict(e) for e in events]
    counts = Counter(e["event_id"] for e in rows)
    duplicates = sorted(k for k, n in counts.items() if n > 1)
    known = set(counts)
    dangling = sorted(
        ({"parent": u, "child": v} for u, v in graph.edges if u not in known or v not in known),
        key=lambda d: (d["parent"], d["child"]),
    )
    cycles = [sorted(c) for c in nx.simple_cycles(graph)] if not nx.is_directed_acyclic_graph(graph) else []

    mismatches: list[dict[str, Any]] = []
    skew: list[dict[str, Any]] = []
    for event in rows:
        child = event["event_id"]
        declared = set(event.get("parent_event_ids") or [])
        recorded = set(graph.predecessors(child)) if child in graph else set()
        if declared != recorded:
            mismatches.append({"event_id": child, "only_in_event": sorted(declared - recorded),
                               "only_in_dependencies": sorted(recorded - declared)})
        for parent in recorded:
            parent_ts = graph.nodes[parent].get("timestamp")
            if parent_ts and event.get("timestamp") and str(parent_ts) > str(event["timestamp"]):
                skew.append({"parent": parent, "child": child})
    return {
        "valid": not (duplicates or dangling or cycles or mismatches),
        "duplicate_event_ids": duplicates,
        "dangling_references": dangling,
        "cycles": cycles,
        "parent_list_mismatches": mismatches,
        "clock_skew_warnings": skew,
        "event_count": len(known),
        "edge_count": graph.number_of_edges(),
    }
