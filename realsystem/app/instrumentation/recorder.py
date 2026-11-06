"""Event + edge recording.

Business logic never writes these rows by hand. Pass the caller's Session and the event commits
or rolls back atomically with the business write -- so a `committed` event exists iff the state
change committed. Edges come only from explicit `Parent`s supplied by the instrumentation point;
nothing here (or anywhere else) derives an edge from timestamps.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import Event, EventDependency
from app.database.session import transaction
from app.instrumentation.context import TraceContext

DEPENDENCY_TYPES = frozenset(
    {
        "request_propagation",
        "message_published",
        "message_consumed",
        "service_execution",
        "database_commit",
        "retry_dependency",
    }
)


@dataclass(frozen=True)
class Parent:
    event_id: str
    dependency_type: str
    evidence_reference: str

    def __post_init__(self) -> None:
        if self.dependency_type not in DEPENDENCY_TYPES:
            raise ValueError(f"unknown dependency type {self.dependency_type!r}")


def record_event(
    session: Session,
    ctx: TraceContext,
    service: str,
    event_type: str,
    parents: Sequence[Parent],
    status: str = "ok",
    message: str = "",
    level: str = "INFO",
    xid: str | None = None,
    metadata: dict[str, Any] | None = None,
    message_id: str | None = None,
    delivery_attempt_id: str | None = None,
) -> str:
    event_id = str(uuid.uuid4())
    meta: dict[str, Any] = {"run_id": ctx.run_id, "label": ctx.label, "message": message, "level": level}
    if ctx.attempt_no is not None:
        meta["attempt_no"] = ctx.attempt_no
    if ctx.message_kind:
        meta["message_kind"] = ctx.message_kind
    meta.update(metadata or {})
    unique_parents = list(dict.fromkeys(p.event_id for p in parents))
    session.add(
        Event(
            event_id=event_id,
            trace_id=ctx.trace_id,
            request_id=ctx.request_id,
            service=service,
            event_type=event_type,
            timestamp=datetime.now(timezone.utc),
            parent_event_ids=unique_parents,
            message_id=message_id or ctx.message_id,
            delivery_attempt_id=delivery_attempt_id or ctx.delivery_attempt_id,
            database_transaction_id=xid,
            status=status,
            event_metadata=meta,
        )
    )
    session.flush()  # the FK on child_event_id needs the event row first
    for p in parents:
        session.add(
            EventDependency(
                parent_event_id=p.event_id,
                child_event_id=event_id,
                dependency_type=p.dependency_type,
                evidence_reference=p.evidence_reference,
            )
        )
    session.flush()
    return event_id


def record_standalone(ctx: TraceContext, service: str, event_type: str, parents: Sequence[Parent], **kw: Any) -> str:
    """Record an event in its own transaction (for events not tied to a business write)."""
    with transaction() as session:
        return record_event(session, ctx, service, event_type, parents, **kw)


def http_parents(ctx: TraceContext, service: str) -> list[Parent]:
    """Edges from the parent ids that arrived in the X-Parent-Event-Ids header."""
    return [Parent(p, "request_propagation", f"http-header:x-parent-event-ids->{service}") for p in ctx.parent_event_ids]
