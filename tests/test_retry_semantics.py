"""Retries, rollbacks and duplicates: reachability is not attribution."""
import pytest

from app.scenarios.ground_truth import committing_attempt
from app.verifier.claims import verify_claim
from tests.helpers import PARTIAL, claim, ev, graph_from, retry_trace


def graph(coverage=None):
    events, kinds = retry_trace()
    return graph_from(events, kinds=kinds) if coverage is None else graph_from(events, coverage, kinds)


@pytest.mark.parametrize("source,target,relationship,expected", [
    # The successful attempt produced the commit.
    ("deliver_2", "commit_2", "produced_commit", "SUPPORTED"),
    ("deliver_2", "commit_2", "not_produced_commit", "CONTRADICTED"),
    # Plausible: attempt 1 wrote first and reaches the commit through the retry edge. It did not produce it.
    ("deliver_1", "commit_2", "produced_commit", "CONTRADICTED"),
    ("deliver_1", "commit_2", "not_produced_commit", "SUPPORTED"),
    ("write_1", "commit_2", "produced_commit", "CONTRADICTED"),
    # A failed attempt still *reaches* the commit: the two questions must not collapse into one.
    ("deliver_1", "commit_2", "reachable", "SUPPORTED"),
    ("rollback_1", "commit_2", "reachable", "SUPPORTED"),
    # Duplicate delivery: downstream of the commit, and produced nothing.
    ("deliver_3", "commit_2", "produced_commit", "CONTRADICTED"),
    ("deliver_3", "commit_2", "not_reachable", "SUPPORTED"),
    ("commit_2", "skip_3", "reachable", "SUPPORTED"),
    # Rolled-back transaction as a claimed commit.
    ("deliver_1", "rollback_1", "produced_commit", "CONTRADICTED"),
    ("deliver_1", "rollback_1", "not_produced_commit", "SUPPORTED"),
    # Not attempt-scoped, or target isn't a DB outcome.
    ("deliver_2", "failed_1", "produced_commit", "INVALID_CLAIM"),
    ("deliver_2", "skip_3", "produced_commit", "INVALID_CLAIM"),
])
def test_retry_attribution(source, target, relationship, expected):
    assert verify_claim(claim(source, target, relationship), graph())["verdict"] == expected


def test_source_outside_any_attempt_cannot_produce_a_commit():
    events = [ev("root"), ev("commit", ["root"], attempt="a1", status="committed", txn="5")]
    assert verify_claim(claim("root", "commit", "produced_commit"), graph_from(events))["verdict"] == "INVALID_CLAIM"


def test_rejection_names_the_retry_edge():
    result = verify_claim(claim("deliver_1", "commit_2", "produced_commit"), graph())
    assert result["same_delivery_attempt"] is False
    assert "retry" in result["explanation"]
    assert any(h["type"] == "retry_dependency" for h in result["evidence"])


def test_rollback_is_never_treated_as_committed_even_within_its_own_attempt():
    result = verify_claim(claim("write_1", "rollback_1", "produced_commit"), graph())
    assert result["verdict"] == "CONTRADICTED"
    assert result["target_committed"] is False and result["same_delivery_attempt"] is True


def test_path_only_through_retry_edge_does_not_count_as_production():
    # Same attempt id on both ends, but the only connection crosses a retry edge.
    events = [ev("s", attempt="a"), ev("r", ["s"], attempt="a"), ev("c", ["r"], attempt="a", status="committed", txn="9")]
    g = graph_from(events, kinds={("s", "r"): "retry_dependency"})
    assert verify_claim(claim("s", "c", "produced_commit"), g)["verdict"] == "CONTRADICTED"
    assert verify_claim(claim("r", "c", "produced_commit"), g)["verdict"] == "SUPPORTED"


def test_edge_with_both_retry_and_ordinary_types_still_counts():
    events = [ev("s", attempt="a"), ev("c", ["s"], attempt="a", status="committed", txn="9")]
    g = graph_from(events)
    g.edges["s", "c"]["dependencies"].append({"type": "retry_dependency", "evidence_reference": "t"})
    assert verify_claim(claim("s", "c", "produced_commit"), g)["verdict"] == "SUPPORTED"


def test_missing_attempt_path_is_not_observed_when_incomplete():
    events = [ev("s", attempt="a"), ev("c", attempt="a", status="committed", txn="9")]
    assert verify_claim(claim("s", "c", "produced_commit"), graph_from(events, PARTIAL))["verdict"] == "NOT_OBSERVED"


def test_failure_does_not_imply_commit_in_partial_view_either():
    assert verify_claim(claim("deliver_1", "commit_2", "produced_commit"), graph(PARTIAL))["verdict"] == "CONTRADICTED"


def test_duplicate_deliveries_stay_one_logical_message():
    events, _ = retry_trace()
    deliveries = [e for e in events if e["event_type"].startswith("deliver")]
    assert len({e["message_id"] for e in deliveries}) == 1
    assert len({e["delivery_attempt_id"] for e in deliveries}) == 3


def _artifact(processed, xact):
    return {"state": {"processed_messages": processed, "xact_status": xact}}


def test_ground_truth_oracle_requires_postgres_to_confirm_commit():
    row = {"commit_event_id": "c", "delivery_attempt_id": "a2", "xid": "10"}
    assert committing_attempt(_artifact([row], {"10": "committed"}), "c") == "a2"
    assert committing_attempt(_artifact([row], {"10": "aborted"}), "c") is None
    assert committing_attempt(_artifact([row], {}), "c") is None
    assert committing_attempt(_artifact([], {}), "c") is None
