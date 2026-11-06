"""Runs the four failure scenarios through the real services and captures the evidence.

Nothing here writes events. Everything in an artifact was recorded by the services themselves; this
module only sends requests, waits for the system to quiesce, and reads the database back.
"""
from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import redis
from sqlalchemy import text

from app.config import CONSUMER_GROUP, GATEWAY_URL, INVENTORY_URL, REDIS_URL, STREAM
from app.database.session import engine
from app.verifier.collector import load_run
from app.verifier.evidence import CoverageMetadata
from app.verifier.graph import build_dependency_dag, validate_graph_integrity


def _seed(sku: str, available: int) -> None:
    httpx.put(f"{INVENTORY_URL}/inventory/{sku}", json={"available": available}, timeout=5).raise_for_status()


def _sku() -> str:
    """Opaque, so an AI-visible log line reveals neither the run nor which request is which."""
    return f"SKU-{uuid.uuid4().hex[:6].upper()}"


def _order(run_id: str, label: str, customer: str, sku: str, amount: int = 4200, faults: dict | None = None, qty: int = 1) -> dict:
    resp = httpx.post(f"{GATEWAY_URL}/orders", timeout=15, json={
        "customer_id": customer, "run_id": run_id, "label": label, "items": [{"sku": sku, "qty": qty}],
        "amount_cents": amount, "faults": faults or {},
    })
    resp.raise_for_status()
    return resp.json()


def wait_quiescent(timeout_s: float = 45.0) -> None:
    """All stream entries delivered and ACKed. Polling with a deadline, not a blind sleep."""
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        group = next((g for g in r.xinfo_groups(STREAM) if g["name"] == CONSUMER_GROUP), None)
        if group is not None and group["pending"] == 0 and (group.get("lag") or 0) == 0:
            return
        time.sleep(0.1)
    raise TimeoutError(f"stream {STREAM} did not quiesce within {timeout_s}s")


def _sql_closure(run_id: str) -> list[list[str]]:
    """All-pairs reachability via a recursive CTE: an oracle sharing no code with the NetworkX verifier."""
    query = text("""
        WITH RECURSIVE run_edges AS (
            SELECT DISTINCT d.parent_event_id AS p, d.child_event_id AS c
            FROM event_dependencies d JOIN events e ON e.event_id = d.child_event_id
            WHERE e.metadata->>'run_id' = :run
        ), reach(a, b) AS (
            SELECT p, c FROM run_edges
            UNION
            SELECT r.a, e.c FROM reach r JOIN run_edges e ON e.p = r.b
        )
        SELECT a, b FROM reach ORDER BY a, b""")
    with engine.connect() as conn:
        return [[row.a, row.b] for row in conn.execute(query, {"run": run_id})]


def _state_snapshot(run_id: str, events: list[dict]) -> dict[str, Any]:
    """Persisted state read directly from the business tables, plus Postgres's own verdict on each xid."""
    with engine.connect() as conn:
        orders = [dict(r._mapping) for r in conn.execute(text(
            "SELECT order_id, label, status, fault_plan, created_event_id, final_commit_event_id, final_commit_attempt_id, final_commit_xid "
            "FROM orders WHERE run_id = :run ORDER BY label"), {"run": run_id})]
        order_ids = [o["order_id"] for o in orders] or [""]
        processed = [dict(r._mapping) for r in conn.execute(text(
            "SELECT message_id, order_id, outcome, delivery_attempt_id, commit_event_id, xid FROM processed_messages "
            "WHERE order_id = ANY(:ids)"), {"ids": order_ids})]
        deliveries = [dict(r._mapping) for r in conn.execute(text(
            "SELECT delivery_attempt_id, message_id, attempt_no, stream_entry_id, delivered_event_id, terminal_event_id, outcome "
            "FROM message_deliveries WHERE message_id = ANY(:ids) ORDER BY message_id, attempt_no"),
            {"ids": sorted({e["message_id"] for e in events if e["message_id"]}) or [""]})]
        xids = sorted({e["database_transaction_id"] for e in events if e["database_transaction_id"]})
        xact_status = {x: conn.execute(text("SELECT pg_xact_status(CAST(:x AS xid8))"), {"x": x}).scalar_one() for x in xids}
    return {"orders": orders, "processed_messages": processed, "message_deliveries": deliveries, "xact_status": xact_status}


def build_manifest(events: list[dict]) -> dict[str, str]:
    """Stable logical name -> generated UUID. Names are derived from recorded fields, in recorded order."""
    manifest: dict[str, str] = {}
    for e in events:
        m = e["metadata"]
        parts = [m.get("label") or "unlabelled"]
        if m.get("message_kind"):
            parts.append(m["message_kind"].split(".")[-1])
        if m.get("attempt_no") is not None:
            parts.append(f"a{m['attempt_no']}")
        parts.append(e["event_type"])
        name = ".".join(parts)
        i, candidate = 1, name
        while candidate in manifest:
            i += 1
            candidate = f"{name}#{i}"
        manifest[candidate] = e["event_id"]
    return manifest


def capture(run_id: str) -> dict[str, Any]:
    events, deps = load_run(run_id)
    graph = build_dependency_dag(events, deps)
    integrity = validate_graph_integrity(graph, events)
    # Closed-world status is earned: the stream is quiescent (checked by the caller) and integrity holds.
    coverage = CoverageMetadata(
        closed_world=integrity["valid"], level="complete" if integrity["valid"] else "partial",
        source="live capture after stream quiescence",
        notes="Complete with respect to the instrumented services and dependency types.",
    )
    return {
        "run_id": run_id, "captured_at_utc": datetime.now(timezone.utc).isoformat(), "events": events,
        "dependencies": deps, "coverage": coverage.model_dump(), "integrity": integrity,
        "manifest": build_manifest(events), "sql_closure": _sql_closure(run_id), "state": _state_snapshot(run_id, events),
    }


def _new_run(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:8]}"


def _concurrently(*calls) -> list[dict]:
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return [f.result() for f in [pool.submit(c) for c in calls]]


def _intervention(name: str, description: str, run_id: str) -> dict[str, Any]:
    wait_quiescent()
    events, _ = load_run(run_id)
    seen = sorted({f"{e['metadata'].get('label')}:{e['event_type']}" for e in events if e["status"] in ("failed", "rolled_back")})
    state = _state_snapshot(run_id, events)
    return {"name": name, "description": description, "run_id": run_id,
            "failure_events": seen, "order_outcomes": {o["label"]: o["status"] for o in state["orders"]},
            "event_count": len(events)}


S1_TIMEOUT = "gateway timeout after 2000ms while authorizing card"
S1_WARNING = "payment-gateway connection pool near saturation (38/40 active), p99 latency 2140ms"


def scenario_1() -> dict[str, Any]:
    """Temporal correlation trap: B warns about the payment gateway just before A's payment fails."""
    run = _new_run("s1")
    sku_a, sku_b, sku_c = _sku(), _sku(), _sku()
    for sku in (sku_a, sku_b, sku_c):
        _seed(sku, 5)
    barrier = {"key": f"barrier:{run}", "parties": 2, "timeout_s": 1.0}
    _concurrently(
        lambda: _order(run, "A", "cust-1041", sku_a, 4200, {"payment_failure_reason": S1_TIMEOUT, "payment_latency_ms": 60, "rendezvous": barrier}),
        lambda: _order(run, "B", "cust-2207", sku_b, 9900, {"warning_text": S1_WARNING, "payment_latency_ms": 180, "rendezvous": barrier}),
        lambda: _order(run, "bg", "cust-3318", sku_c, 1500),
    )
    wait_quiescent()
    artifact = capture(run)

    # Interventions: same system, one factor changed. This is the only causal evidence DAGScope reports.
    r1 = _new_run("s1-no-B")
    sku_r1 = _sku()
    _seed(sku_r1, 5)
    _order(r1, "A", "cust-1041", sku_r1, 4200, {"payment_failure_reason": S1_TIMEOUT, "payment_latency_ms": 60})
    r2 = _new_run("s1-no-A-fault")
    sku_r2a, sku_r2b = _sku(), _sku()
    _seed(sku_r2a, 5)
    _seed(sku_r2b, 5)
    barrier2 = {"key": f"barrier:{r2}", "parties": 2, "timeout_s": 1.0}
    _concurrently(
        lambda: _order(r2, "A", "cust-1041", sku_r2a, 4200, {"payment_latency_ms": 60, "rendezvous": barrier2}),
        lambda: _order(r2, "B", "cust-2207", sku_r2b, 9900, {"warning_text": S1_WARNING, "payment_latency_ms": 180, "rendezvous": barrier2}),
    )
    artifact["interventions"] = [
        _intervention("remove_request_B", "Request A alone, same injected fault, no request B and no warning.", r1),
        _intervention("remove_fault_on_A", "Requests A and B overlap with B's warning, but no fault injected on A.", r2),
    ]
    return artifact


def scenario_2() -> dict[str, Any]:
    """Retry trap: attempt 1 writes then rolls back; attempt 2 commits but is not ACKed; attempt 3 is a duplicate."""
    run = _new_run("s2")
    sku_a, sku_c = _sku(), _sku()
    _seed(sku_a, 5)
    _seed(sku_c, 5)
    _concurrently(
        lambda: _order(run, "A", "cust-4410", sku_a, 7300, {"rollback_attempts": [1], "skip_ack_after_commit_attempts": [2]}),
        lambda: _order(run, "bg", "cust-5120", sku_c, 1200),
    )
    wait_quiescent()
    return capture(run)


def scenario_3() -> dict[str, Any]:
    """Partial observability: a clean execution, later exported with events or links removed."""
    run = _new_run("s3")
    sku_a, sku_c = _sku(), _sku()
    _seed(sku_a, 5)
    _seed(sku_c, 5)
    _concurrently(
        lambda: _order(run, "A", "cust-6001", sku_a, 3100),
        lambda: _order(run, "bg", "cust-6002", sku_c, 2100),
    )
    wait_quiescent()
    return capture(run)


S4_HOLD = "authorization hold rejected for order"


def scenario_4() -> dict[str, Any]:
    """Shared ancestor: one order fans out to reserve and charge; both fail close together, independently."""
    run = _new_run("s4")
    sku_a, sku_c = _sku(), _sku()
    _seed(sku_a, 0)
    _seed(sku_c, 5)
    _concurrently(
        lambda: _order(run, "A", "cust-7777", sku_a, 5600, {"split_fulfilment": True, "payment_failure_reason": S4_HOLD, "payment_latency_ms": 40}),
        lambda: _order(run, "bg", "cust-7778", sku_c, 800),
    )
    wait_quiescent()
    artifact = capture(run)

    r1 = _new_run("s4-stock-ok")
    sku_r1 = _sku()
    _seed(sku_r1, 5)
    _order(r1, "A", "cust-7777", sku_r1, 5600, {"split_fulfilment": True, "payment_failure_reason": S4_HOLD, "payment_latency_ms": 40})
    r2 = _new_run("s4-payment-ok")
    sku_r2 = _sku()
    _seed(sku_r2, 0)
    _order(r2, "A", "cust-7777", sku_r2, 5600, {"split_fulfilment": True, "payment_latency_ms": 40})
    artifact["interventions"] = [
        _intervention("remove_stock_failure", "Stock available, so no reservation failure; same payment fault.", r1),
        _intervention("remove_payment_failure", "No stock, so reservation still fails; no payment fault.", r2),
    ]
    return artifact


SCENARIOS = {"scenario_1": scenario_1, "scenario_2": scenario_2, "scenario_3": scenario_3, "scenario_4": scenario_4}


def run_and_save(out_dir: Path, only: list[str] | None = None) -> dict[str, dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for scenario_id, fn in SCENARIOS.items():
        if only and scenario_id not in only:
            continue
        artifact = {"scenario_id": scenario_id, **fn()}
        (out_dir / f"{scenario_id}.json").write_text(json.dumps(artifact, indent=1, default=str))
        results[scenario_id] = artifact
    return results
