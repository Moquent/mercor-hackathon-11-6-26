"""Open-world semantics: missing edges never prove disconnection."""
import pytest

from app.scenarios.ground_truth import apply_mask
from app.verifier.claims import verify_claim
from app.verifier.evidence import CoverageMetadata
from app.verifier.graph import build_dependency_dag
from app.verifier.reachability import verify_reachability
from tests.helpers import COMPLETE, PARTIAL, claim, ev, graph_from


def chain():
    return [ev("x"), ev("h1", ["x"]), ev("h2", ["h1"]), ev("y", ["h2"]), ev("other")]


def holed(coverage=PARTIAL):
    # h1 was dropped by the export; its edges are gone with it.
    events = [ev("x"), ev("h2"), ev("y", ["h2"]), ev("other")]
    return graph_from(events, coverage)


@pytest.mark.parametrize("source,target,relationship,expected", [
    # Missing evidence is not disproof.
    ("x", "y", "reachable", "NOT_OBSERVED"),
    ("x", "y", "not_reachable", "NOT_OBSERVED"),
    ("other", "y", "not_reachable", "NOT_OBSERVED"),
    ("x", "y", "direct_dependency", "NOT_OBSERVED"),
    # Recognising the gap is the correct answer.
    ("x", "y", "insufficient_evidence", "SUPPORTED"),
    ("other", "y", "insufficient_evidence", "SUPPORTED"),
    # Partial coverage still proves what it shows, so "insufficient" is wrong for a visible path.
    ("h2", "y", "reachable", "SUPPORTED"),
    ("h2", "y", "not_reachable", "CONTRADICTED"),
    ("h2", "y", "insufficient_evidence", "CONTRADICTED"),
    # A hidden id isn't in the evidence.
    ("h1", "y", "reachable", "INVALID_CLAIM"),
])
def test_partial_coverage_verdicts(source, target, relationship, expected):
    assert verify_claim(claim(source, target, relationship), holed())["verdict"] == expected


def test_same_absence_is_contradicted_only_under_closed_world():
    assert verify_claim(claim("x", "y"), holed(COMPLETE))["verdict"] == "CONTRADICTED"
    assert verify_claim(claim("x", "y"), holed(PARTIAL))["verdict"] == "NOT_OBSERVED"


@pytest.mark.parametrize("coverage", [None, {}, {"closed_world": False}, CoverageMetadata()])
def test_unknown_coverage_is_never_treated_as_complete(coverage):
    graph = build_dependency_dag([ev("x"), ev("y")], [], coverage)
    result = verify_reachability("x", "y", graph)
    assert result["verdict"] == "NOT_OBSERVED"
    assert result["coverage"]["closed_world"] is False


def test_trace_id_sharing_alone_proves_nothing():
    graph = graph_from([ev("x", trace_id="t1", message_id="m"), ev("y", trace_id="t1", message_id="m")], PARTIAL)
    assert verify_claim(claim("x", "y"), graph)["verdict"] == "NOT_OBSERVED"


def _artifact():
    events = chain()
    deps = [{"parent_event_id": p, "child_event_id": e["event_id"], "dependency_type": "service_execution", "evidence_reference": "t"}
            for e in events for p in e["parent_event_ids"]]
    return {"events": events, "dependencies": deps, "manifest": {e["event_id"]: e["event_id"] for e in events}}


def test_mask_hides_event_and_can_keep_dangling_reference():
    events, deps, hidden = apply_mask(_artifact(), {"hide_events": ["h1"], "scrub_dangling_refs": False})
    assert hidden == ["h1"]
    assert "h1" not in {e["event_id"] for e in events}
    assert next(e for e in events if e["event_id"] == "h2")["parent_event_ids"] == ["h1"]
    assert not [d for d in deps if "h1" in (d["parent_event_id"], d["child_event_id"])]


def test_mask_can_scrub_references():
    events, _, _ = apply_mask(_artifact(), {"hide_events": ["h1"], "scrub_dangling_refs": True})
    assert next(e for e in events if e["event_id"] == "h2")["parent_event_ids"] == []


def test_mask_can_hide_a_link_but_keep_both_events():
    events, deps, hidden = apply_mask(_artifact(), {"hide_edges": [["h1", "h2"]]})
    assert hidden == [] and {"h1", "h2"} <= {e["event_id"] for e in events}
    assert ("h1", "h2") not in {(d["parent_event_id"], d["child_event_id"]) for d in deps}


def test_masking_does_not_mutate_the_private_artifact():
    art = _artifact()
    before = [list(e["parent_event_ids"]) for e in art["events"]]
    apply_mask(art, {"hide_events": ["h1"], "hide_edges": [["h2", "y"]]})
    assert before == [e["parent_event_ids"] for e in art["events"]]


def test_private_graph_can_hold_a_truth_the_public_view_cannot_prove():
    art = _artifact()
    full = build_dependency_dag(art["events"], art["dependencies"], COMPLETE)
    events, deps, _ = apply_mask(art, {"hide_events": ["h1"]})
    public = build_dependency_dag(events, deps, PARTIAL)
    assert verify_claim(claim("x", "y"), full)["verdict"] == "SUPPORTED"
    assert verify_claim(claim("x", "y"), public)["verdict"] == "NOT_OBSERVED"
