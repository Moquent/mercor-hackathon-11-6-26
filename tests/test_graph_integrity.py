"""Instrumentation-error detection, and the rule that time never creates an edge."""
import random

import networkx as nx
import pytest

from app.verifier.claims import verify_claim
from app.verifier.graph import build_dependency_dag, validate_graph_integrity
from tests.helpers import COMPLETE, claim, dep, ev, graph_from, temporal_trace


def test_valid_trace_passes():
    events = temporal_trace()
    report = validate_graph_integrity(graph_from(events), events)
    assert report["valid"] and report["event_count"] == 7 and report["edge_count"] == 5
    assert report["dangling_references"] == [] and report["cycles"] == []


def test_dangling_parent_is_reported_and_is_not_a_valid_endpoint():
    events = [ev("a", ["missing_parent"])]
    graph = graph_from(events)
    report = validate_graph_integrity(graph, events)
    assert not report["valid"]
    assert report["dangling_references"] == [{"parent": "missing_parent", "child": "a"}]
    assert verify_claim(claim("missing_parent", "a"), graph)["verdict"] == "INVALID_CLAIM"


def test_cycle_is_reported():
    events = [ev("a", ["c"]), ev("b", ["a"]), ev("c", ["b"])]
    graph = graph_from(events)
    report = validate_graph_integrity(graph, events)
    assert not report["valid"]
    assert not nx.is_directed_acyclic_graph(graph)
    assert report["cycles"] == [["a", "b", "c"]]


def test_duplicate_event_id_is_reported():
    events = [ev("a"), ev("a"), ev("b", ["a"])]
    report = validate_graph_integrity(graph_from(events), events)
    assert not report["valid"] and report["duplicate_event_ids"] == ["a"]


def test_parent_list_disagreeing_with_edge_table_is_reported():
    events = [ev("a"), ev("b", ["a"])]
    report = validate_graph_integrity(build_dependency_dag(events, []), events)
    assert not report["valid"]
    assert report["parent_list_mismatches"] == [{"event_id": "b", "only_in_event": ["a"], "only_in_dependencies": []}]


@pytest.mark.parametrize("bad_events", [
    [ev("a", ["ghost"]), ev("z")],
    [ev("a", ["b"]), ev("b", ["a"]), ev("z")],
    [ev("a"), ev("a"), ev("z")],
])
def test_integrity_failure_revokes_closed_world(bad_events):
    """A broken trace can no longer support a negative conclusion, whatever its metadata claims."""
    result = verify_claim(claim("z", "a"), graph_from(bad_events, COMPLETE))
    assert result["verdict"] == "NOT_OBSERVED"
    assert result["integrity_warnings"]
    assert result["coverage"]["closed_world"] is False


def test_clock_skew_is_a_warning_not_an_error():
    events = [ev("late_parent", ts="2026-11-06T12:00:09+00:00"), ev("early_child", ["late_parent"], ts="2026-11-06T12:00:01+00:00")]
    report = validate_graph_integrity(graph_from(events), events)
    assert report["valid"]
    assert report["clock_skew_warnings"] == [{"parent": "late_parent", "child": "early_child"}]
    # The recorded edge still holds even though the child's timestamp is earlier.
    assert verify_claim(claim("late_parent", "early_child"), graph_from(events))["verdict"] == "SUPPORTED"


# ---- Timestamp proximity never creates an edge -------------------------------------------------

def test_identical_timestamps_create_no_edges():
    events = [ev(f"e{i}", ts="2026-11-06T12:00:00+00:00") for i in range(6)]
    assert build_dependency_dag(events, []).number_of_edges() == 0


@pytest.mark.parametrize("seed", range(8))
def test_graph_is_invariant_under_timestamp_permutation(seed):
    events = temporal_trace()
    rng = random.Random(seed)
    shuffled = [{**e, "timestamp": f"2026-11-06T12:{rng.randrange(60):02d}:{rng.randrange(60):02d}+00:00"} for e in events]
    a, b = graph_from(events), graph_from(shuffled)
    assert set(a.edges) == set(b.edges)
    assert verify_claim(claim("warn_b", "pay_fail_a"), b)["verdict"] == "CONTRADICTED"
    assert verify_claim(claim("req_a", "pay_fail_a"), b)["verdict"] == "SUPPORTED"


def test_multiple_parents_and_types_are_preserved():
    events = [ev("a"), ev("b"), ev("c", ["a", "b"])]
    deps = [dep("a", "c", "message_consumed"), dep("a", "c", "retry_dependency"), dep("b", "c", "database_commit")]
    graph = build_dependency_dag(events, deps, COMPLETE)
    assert set(graph.predecessors("c")) == {"a", "b"}
    assert {d["type"] for d in graph.edges["a", "c"]["dependencies"]} == {"message_consumed", "retry_dependency"}
    assert {h["type"] for h in verify_claim(claim("a", "c"), graph)["evidence"]} == {"message_consumed", "retry_dependency"}
