"""Reachability verdicts on hand-specified graphs."""
import pytest

from app.verifier.claims import verify_claim, verify_claims
from app.verifier.reachability import (explain_dependency_path, get_ancestors, get_descendants, nearest_common_ancestors,
                                       verify_reachability)
from tests.helpers import COMPLETE, PARTIAL, claim, ev, graph_from, shared_trace, temporal_trace


@pytest.mark.parametrize("source,target,relationship,expected", [
    # True claims accepted.
    ("req_a", "pay_fail_a", "reachable", "SUPPORTED"),
    ("order_a", "pay_fail_a", "reachable", "SUPPORTED"),
    ("req_b", "pay_fail_a", "not_reachable", "SUPPORTED"),
    # Plausible but false: the warning sits right before the failure.
    ("warn_b", "pay_fail_a", "reachable", "CONTRADICTED"),
    ("req_b", "pay_fail_a", "reachable", "CONTRADICTED"),
    ("order_b", "pay_fail_a", "direct_dependency", "CONTRADICTED"),
    # Reversed direction is a different claim.
    ("pay_fail_a", "req_a", "reachable", "CONTRADICTED"),
    # False negative on a true path.
    ("req_a", "pay_fail_a", "not_reachable", "CONTRADICTED"),
    # A complete trace leaves nothing undecidable.
    ("req_b", "pay_fail_a", "insufficient_evidence", "CONTRADICTED"),
    ("req_a", "pay_fail_a", "insufficient_evidence", "CONTRADICTED"),
])
def test_temporal_trap_verdicts(source, target, relationship, expected):
    graph = graph_from(temporal_trace())
    assert verify_claim(claim(source, target, relationship), graph)["verdict"] == expected


@pytest.mark.parametrize("source,target,relationship,expected", [
    ("pay_req_a", "pay_fail_a", "direct_dependency", "SUPPORTED"),
    ("req_a", "pay_fail_a", "direct_dependency", "CONTRADICTED"),  # transitive, not direct
    ("req_a", "pay_fail_a", "reachable", "SUPPORTED"),
])
def test_direct_and_transitive_are_distinguished(source, target, relationship, expected):
    assert verify_claim(claim(source, target, relationship), graph_from(temporal_trace()))["verdict"] == expected


def test_transitive_rejection_explains_itself():
    result = verify_claim(claim("req_a", "pay_fail_a", "direct_dependency"), graph_from(temporal_trace()))
    assert "transitive" in result["explanation"]


@pytest.mark.parametrize("bad_claim", [
    claim("ghost", "pay_fail_a"),
    claim("req_a", "ghost"),
    claim("ghost", "phantom", "not_reachable"),
    claim("ghost", "pay_fail_a", "insufficient_evidence"),
    claim("req_a", "req_a"),
    claim("req_a", "pay_fail_a", "caused"),
    claim("req_a", "pay_fail_a", "root_cause"),
    claim("", "pay_fail_a"),
    {"source_event_id": "req_a"},
    {"source_event_id": 7, "target_event_id": None, "relationship": "reachable"},
])
def test_malformed_or_unevaluable_claims_are_invalid(bad_claim):
    assert verify_claim(bad_claim, graph_from(temporal_trace()))["verdict"] == "INVALID_CLAIM"


def test_causal_claims_are_refused_even_when_a_path_exists():
    result = verify_claim(claim("req_a", "pay_fail_a", "caused"), graph_from(temporal_trace()))
    assert result["verdict"] == "INVALID_CLAIM"
    assert "intervention" in result["explanation"]
    assert verify_claim(claim("req_a", "pay_fail_a"), graph_from(temporal_trace()))["evidence_class"] == "recorded_dependency_reachability"


def test_supported_result_carries_a_valid_path_and_per_hop_evidence():
    graph = graph_from(temporal_trace())
    result = verify_reachability("req_a", "pay_fail_a", graph)
    assert result["verdict"] == "SUPPORTED"
    assert result["observed_reachable"] is True
    assert result["dependency_path"] == ["req_a", "order_a", "pay_req_a", "pay_fail_a"]
    assert [(h["parent"], h["child"]) for h in result["evidence"]] == [("req_a", "order_a"), ("order_a", "pay_req_a"), ("pay_req_a", "pay_fail_a")]
    assert all(graph.has_edge(h["parent"], h["child"]) for h in result["evidence"])
    assert result["claim"] == {"source": "req_a", "target": "pay_fail_a", "relationship": "reachable"}


def test_claims_are_independently_evaluated():
    graph = graph_from(temporal_trace())
    results = verify_claims([claim("req_a", "pay_fail_a"), claim("warn_b", "pay_fail_a"), claim("ghost", "pay_fail_a"),
                             claim("req_b", "pay_fail_a", "not_reachable"), claim("req_a", "pay_fail_a", "caused")], graph)
    assert [r["verdict"] for r in results] == ["SUPPORTED", "CONTRADICTED", "INVALID_CLAIM", "SUPPORTED", "INVALID_CLAIM"]


def test_ancestors_and_descendants():
    graph = graph_from(temporal_trace())
    assert get_ancestors("pay_fail_a", graph) == {"req_a", "order_a", "pay_req_a"}
    assert get_descendants("req_b", graph) == {"order_b", "warn_b"}
    assert get_ancestors("ghost", graph) == set()


def test_explain_path_with_and_without_a_path():
    graph = graph_from(temporal_trace())
    assert explain_dependency_path("req_a", "order_a", graph)["path"] == ["req_a", "order_a"]
    assert explain_dependency_path("warn_b", "pay_fail_a", graph)["path"] == []


# ---- Shared ancestry --------------------------------------------------------------------------

@pytest.mark.parametrize("source,target,relationship,ancestor,expected", [
    ("x", "y", "shared_ancestor", "z", "SUPPORTED"),
    ("x", "y", "shared_ancestor", "root", "SUPPORTED"),
    ("x", "y", "shared_ancestor", None, "SUPPORTED"),
    ("x", "y", "no_shared_ancestor", None, "CONTRADICTED"),
    # Plausible: x_mid looks upstream, but only of x.
    ("x", "y", "shared_ancestor", "x_mid", "CONTRADICTED"),
    ("x", "y", "shared_ancestor", "other", "CONTRADICTED"),
    ("x", "other", "shared_ancestor", None, "CONTRADICTED"),
    ("x", "other", "no_shared_ancestor", None, "SUPPORTED"),
    # Shared ancestry must not leak into either direct direction.
    ("x", "y", "reachable", None, "CONTRADICTED"),
    ("y", "x", "reachable", None, "CONTRADICTED"),
    ("x", "y", "direct_dependency", None, "CONTRADICTED"),
    ("x", "y", "not_reachable", None, "SUPPORTED"),
    ("y", "x", "not_reachable", None, "SUPPORTED"),
    # Degenerate forms.
    ("x", "y", "shared_ancestor", "x", "INVALID_CLAIM"),
    ("x", "x", "shared_ancestor", "z", "INVALID_CLAIM"),
    ("x", "y", "shared_ancestor", "ghost", "INVALID_CLAIM"),
])
def test_shared_ancestor_verdicts(source, target, relationship, ancestor, expected):
    assert verify_claim(claim(source, target, relationship, ancestor), graph_from(shared_trace()))["verdict"] == expected


def test_shared_ancestor_result_reports_both_directions_and_nearest():
    result = verify_claim(claim("x", "y", "shared_ancestor", "z"), graph_from(shared_trace()))
    assert result["x_reaches_y"] is False and result["y_reaches_x"] is False
    assert result["nearest_common_ancestors"] == ["z"]
    assert result["dependency_path"] == ["z", "x_mid", "x"] and result["second_path"] == ["z", "y_mid", "y"]


def test_nearest_common_ancestor_excludes_higher_ones():
    assert nearest_common_ancestors("x", "y", graph_from(shared_trace())) == ["z"]


def test_shared_ancestor_is_not_observed_when_incomplete():
    assert verify_claim(claim("x", "other", "shared_ancestor"), graph_from(shared_trace(), PARTIAL))["verdict"] == "NOT_OBSERVED"
    assert verify_claim(claim("x", "other", "no_shared_ancestor"), graph_from(shared_trace(), PARTIAL))["verdict"] == "NOT_OBSERVED"


def test_explicit_coverage_argument_overrides_graph_attribute():
    graph = graph_from(temporal_trace(), PARTIAL)
    assert verify_reachability("warn_b", "pay_fail_a", graph)["verdict"] == "NOT_OBSERVED"
    assert verify_reachability("warn_b", "pay_fail_a", graph, COMPLETE)["verdict"] == "CONTRADICTED"
    assert ev("q")["event_id"] == "q"
