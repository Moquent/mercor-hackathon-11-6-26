"""PostgreSQL schema: business state plus the append-only observability tables."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Event(Base):
    """Append-only. A DB trigger rejects UPDATE/DELETE (see session.py)."""

    __tablename__ = "events"
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(36), index=True)
    request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    service: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(64))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    parent_event_ids: Mapped[list] = mapped_column(JSONB, default=list)
    message_id: Mapped[str | None] = mapped_column(String(36), index=True)
    delivery_attempt_id: Mapped[str | None] = mapped_column(String(36), index=True)
    database_transaction_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    event_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class EventDependency(Base):
    """Directed edge: parent_event -> dependent_child_event.

    parent_event_id deliberately has no FK: a dangling parent is an instrumentation
    error that validate_graph_integrity must be able to detect and report.
    """

    __tablename__ = "event_dependencies"
    parent_event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    child_event_id: Mapped[str] = mapped_column(String(36), ForeignKey("events.event_id"), primary_key=True)
    dependency_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    evidence_reference: Mapped[str] = mapped_column(Text)


class Order(Base):
    __tablename__ = "orders"
    order_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(36))
    request_id: Mapped[str] = mapped_column(String(36))
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(32))
    customer_id: Mapped[str] = mapped_column(String(64))
    items: Mapped[list] = mapped_column(JSONB)
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    # Controlled fault injection is always labelled on the record it affected.
    fault_plan: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_event_id: Mapped[str] = mapped_column(String(36))
    created_xid: Mapped[str] = mapped_column(String(32))
    # Set only in the transaction that committed the final state change.
    final_commit_event_id: Mapped[str | None] = mapped_column(String(36))
    final_commit_attempt_id: Mapped[str | None] = mapped_column(String(36))
    final_commit_xid: Mapped[str | None] = mapped_column(String(32))


class InventoryItem(Base):
    __tablename__ = "inventory"
    sku: Mapped[str] = mapped_column(String(128), primary_key=True)
    available: Mapped[int] = mapped_column(Integer)
    # The committed write this row currently reflects: a reader's real data dependency.
    last_commit_event_id: Mapped[str | None] = mapped_column(String(36))


class Reservation(Base):
    __tablename__ = "reservations"
    order_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sku: Mapped[str] = mapped_column(String(128), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    commit_event_id: Mapped[str] = mapped_column(String(36))
    xid: Mapped[str] = mapped_column(String(32))


class Payment(Base):
    __tablename__ = "payments"
    order_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    commit_event_id: Mapped[str] = mapped_column(String(36))


class MessageDelivery(Base):
    """One row per delivery attempt; logical message identity lives in message_id."""

    __tablename__ = "message_deliveries"
    __table_args__ = (UniqueConstraint("message_id", "attempt_no"),)
    delivery_attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(36), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    stream_entry_id: Mapped[str] = mapped_column(String(32))
    consumer: Mapped[str] = mapped_column(String(64))
    delivered_event_id: Mapped[str] = mapped_column(String(36))
    terminal_event_id: Mapped[str | None] = mapped_column(String(36))
    outcome: Mapped[str | None] = mapped_column(String(32))


class ProcessedMessage(Base):
    """Idempotency ledger. The PK guarantees one committed effect per logical message."""

    __tablename__ = "processed_messages"
    message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(36))
    outcome: Mapped[str] = mapped_column(String(32))
    delivery_attempt_id: Mapped[str] = mapped_column(String(36))
    commit_event_id: Mapped[str] = mapped_column(String(36))
    xid: Mapped[str] = mapped_column(String(32))
