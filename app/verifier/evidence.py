"""Coverage metadata and the structured verdict shape. Verdicts always carry their evidence."""
from __future__ import annotations

from enum import Enum
from typing import Any

import networkx as nx
from pydantic import BaseModel


class Verdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    NOT_OBSERVED = "NOT_OBSERVED"
    INVALID_CLAIM = "INVALID_CLAIM"


class CoverageMetadata(BaseModel):
    """Whether absence of an edge in this graph means anything.

    closed_world=True asserts every instrumented event and dependency of the execution was captured.
    It is the only thing that licenses a negative conclusion, and it must be stated explicitly: an
    unknown coverage is treated as open-world, never as complete.
    """

    closed_world: bool = False
    level: str = "unknown"  # complete | partial | unknown
    source: str = "unspecified"
    notes: str = ""


UNKNOWN_COVERAGE = CoverageMetadata(closed_world=False, level="unknown", source="default",
                                    notes="no coverage metadata supplied; treated as incomplete")


def resolve_coverage(graph: nx.DiGraph, coverage: CoverageMetadata | dict | None) -> tuple[CoverageMetadata, list[str]]:
    """Explicit argument > graph attribute > open-world. Integrity failures revoke closed-world status."""
    raw = coverage if coverage is not None else graph.graph.get("coverage")
    if raw is None:
        cov = UNKNOWN_COVERAGE
    elif isinstance(raw, CoverageMetadata):
        cov = raw
    else:
        cov = CoverageMetadata.model_validate(raw)
    warnings: list[str] = []
    issues = graph.graph.get("structural_issues", [])
    if issues:
        warnings = list(issues)
        if cov.closed_world:
            cov = cov.model_copy(update={
                "closed_world": False, "level": "partial",
                "notes": "closed-world claim revoked: the trace failed integrity validation",
            })
    return cov, warnings


def edge_evidence(graph: nx.DiGraph, path: list[str]) -> list[dict[str, Any]]:
    """One record per path hop, including every dependency type recorded on that edge."""
    out = []
    for parent, child in zip(path, path[1:]):
        for dep in graph.edges[parent, child]["dependencies"]:
            out.append({"parent": parent, "child": child, "type": dep["type"], "evidence_reference": dep["evidence_reference"]})
    return out


def make_result(
    claim: dict[str, Any],
    verdict: Verdict,
    explanation: str,
    observed_reachable: bool | None = None,
    dependency_path: list[str] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    coverage: CoverageMetadata | None = None,
    warnings: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "claim": claim,
        "verdict": verdict.value,
        "observed_reachable": observed_reachable,
        "dependency_path": dependency_path or [],
        "evidence": evidence or [],
        "explanation": explanation,
        "coverage": coverage.model_dump() if coverage else None,
        "integrity_warnings": warnings or [],
        # Reachability is observational. It is never reported as counterfactual causation.
        "evidence_class": "recorded_dependency_reachability",
        **extra,
    }
