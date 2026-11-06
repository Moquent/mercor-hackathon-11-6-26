"""Tests against the live stack. They skip, with a stated reason, if the services are not reachable."""
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import redis
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import GATEWAY_URL, INVENTORY_URL, REDIS_URL, STREAM
from app.database.session import engine
from app.scenarios.runner import _order, _seed, _state_snapshot, build_manifest, wait_quiescent
from app.verifier.claims import verify_claim
from app.verifier.collector import load_run
from app.verifier.evidence import CoverageMetadata
from app.verifier.graph import build_dependency_dag, validate_graph_integrity


def _stack_up() -> bool:
    try:
        return httpx.get(f"{GATEWAY_URL}/health", timeout=1).status_code == 200 and httpx.get(f"{INVENTORY_URL}/health", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _stack_up(), reason="live stack not running (docker compose up --build)")
COMPLETE = CoverageMetadata(closed_world=True, level="complete", source="test")


def run_id() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


def captured(run):
    wait_quiescent()
    events, deps = load_run(run)
    graph = build_dependency_dag(events, deps, COMPLETE)
    return events, graph, build_manifest(events), _state_snapshot(run, events)


def test_happy_path_spans_services_and_commits():
    run = run_id()
    _seed(f"{run}-a", 3)
    _order(run, "A", "c", f"{run}-a")
    events, graph, names, state = captured(run)
    assert validate_graph_integrity(graph, events)["valid"]
    assert {"gateway", "orders", "inventory", "worker"} == {e["service"] for e in events}
    assert state["orders"][0]["status"] == "confirmed"
    assert httpx.get(f"{INVENTORY_URL}/inventory/{run}-a", timeout=5).json()["available"] == 2
    assert verify_claim({"source_event_id": names["A.request_received"], "target_event_id": names["A.created.a1.order_state_committed"], "relationship": "reachable"}, graph)["verdict"] == "SUPPORTED"


def test_injected_order_rollback_leaves_no_row_and_no_commit_event():
    run = run_id()
    _seed(f"{run}-a", 3)
    response = _order(run, "A", "c", f"{run}-a", faults={"fail_order_commit": True})
    assert response["order_id"] is None and response["order_status_code"] == 500
    events, graph, _, state = captured(run)
    assert state["orders"] == []
    assert "order_created" not in {e["event_type"] for e in events}, "rolled-back write surfaced as committed"
    rollback = next(e for e in events if e["event_type"] == "db_transaction_rolled_back")
    assert state["xact_status"][rollback["database_transaction_id"]] == "aborted"
    assert validate_graph_integrity(graph, events)["valid"]


def test_concurrent_requests_contend_for_last_unit_without_overselling():
    run = run_id()
    sku = f"{run}-last"
    _seed(sku, 2)
    labels = [f"r{i}" for i in range(6)]
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda label: _order(run, label, label, sku), labels))
    events, graph, _, state = captured(run)
    outcomes = sorted(o["status"] for o in state["orders"])
    assert outcomes.count("confirmed") == 2 and outcomes.count("rejected_insufficient_stock") == 4
    assert httpx.get(f"{INVENTORY_URL}/inventory/{sku}", timeout=5).json()["available"] == 0
    assert validate_graph_integrity(graph, events)["valid"]
    # Each loser's failure records the committed write it read: a real cross-request dependency.
    commits = {e["event_id"] for e in events if e["event_type"] == "reservation_committed"}
    for failure in (e for e in events if e["event_type"] == "reservation_failed"):
        assert set(failure["parent_event_ids"]) & commits


def test_duplicate_publish_is_one_logical_operation():
    run = run_id()
    _seed(f"{run}-a", 3)
    _order(run, "A", "c", f"{run}-a", faults={"duplicate_publish": True})
    events, graph, _, state = captured(run)
    assert len({e["message_id"] for e in events if e["event_type"] == "message_published"}) == 1
    assert len(state["processed_messages"]) == 1
    assert sum(e["event_type"] == "order_state_committed" for e in events) == 1
    deliveries = [e for e in events if e["event_type"] == "message_delivered"]
    assert len(deliveries) == 2 and len({e["message_id"] for e in deliveries}) == 1
    assert len({e["delivery_attempt_id"] for e in deliveries}) == 2
    assert httpx.get(f"{INVENTORY_URL}/inventory/{run}-a", timeout=5).json()["available"] == 2
    assert validate_graph_integrity(graph, events)["valid"]


def test_transient_failure_retries_with_retry_edge():
    run = run_id()
    _seed(f"{run}-a", 3)
    _order(run, "A", "c", f"{run}-a", faults={"transient_fail_attempts": [1]})
    events, graph, names, state = captured(run)
    assert graph.edges[names["A.created.a1.processing_failed"], names["A.created.a2.message_delivered"]]["dependencies"][0]["type"] == "retry_dependency"
    assert state["orders"][0]["final_commit_attempt_id"] == next(e["delivery_attempt_id"] for e in events if e["event_id"] == names["A.created.a2.message_delivered"])


def test_events_table_is_append_only():
    with engine.connect() as conn:
        event_id = conn.execute(text("SELECT event_id FROM events LIMIT 1")).scalar()
    if event_id is None:
        pytest.skip("no events yet")
    for statement in ("UPDATE events SET status='tampered' WHERE event_id=:e", "DELETE FROM events WHERE event_id=:e"):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text(statement), {"e": event_id})


def test_redis_stream_stays_drained():
    wait_quiescent()
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    assert r.xpending(STREAM, "workers")["pending"] == 0
