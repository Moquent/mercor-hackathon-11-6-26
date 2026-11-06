"""Collects recorded events from PostgreSQL. Reads only the observability tables."""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.database.session import engine
from app.verifier.evidence import CoverageMetadata
from app.verifier.graph import build_dependency_dag


def _event_row(row: Any) -> dict[str, Any]:
    d = dict(row._mapping)
    d["timestamp"] = d["timestamp"].isoformat()
    return d


def load_run(run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with engine.connect() as conn:
        events = [_event_row(r) for r in conn.execute(text(
            "SELECT event_id, trace_id, request_id, service, event_type, timestamp, parent_event_ids, message_id, "
            "delivery_attempt_id, database_transaction_id, status, metadata FROM events "
            "WHERE metadata->>'run_id' = :run ORDER BY timestamp, event_id"), {"run": run_id})]
        deps = [dict(r._mapping) for r in conn.execute(text(
            "SELECT d.parent_event_id, d.child_event_id, d.dependency_type, d.evidence_reference "
            "FROM event_dependencies d JOIN events e ON e.event_id = d.child_event_id "
            "WHERE e.metadata->>'run_id' = :run"), {"run": run_id})]
    return events, deps


def graph_for_run(run_id: str, coverage: CoverageMetadata | None = None):
    events, deps = load_run(run_id)
    return build_dependency_dag(events, deps, coverage), events, deps
