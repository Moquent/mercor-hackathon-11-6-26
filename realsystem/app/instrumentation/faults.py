"""Controlled fault injection. Every plan is labelled and persisted on orders.fault_plan."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Rendezvous(BaseModel):
    """Harness-only timing control so two workers overlap without arbitrary sleeps.

    It is deliberately not an application dependency: it fixes *when* events happen, never
    *whether* they happen (it times out and proceeds). Intervention runs omit it entirely.
    """

    key: str
    parties: int = 2
    timeout_s: float = 1.0


class FaultPlan(BaseModel):
    payment_failure_reason: str | None = None
    payment_latency_ms: int = Field(default=0, ge=0, le=1000)  # simulated gateway latency
    warning_text: str | None = None  # emitted around the payment call; no effect on the outcome
    transient_fail_attempts: list[int] = []  # raise before any write on these attempt numbers
    rollback_attempts: list[int] = []  # write, then abort the final transaction
    skip_ack_after_commit_attempts: list[int] = []  # commit but never XACK: forces redelivery
    duplicate_publish: bool = False  # XADD the same logical message twice
    fail_order_commit: bool = False  # roll back the order-creation transaction itself
    rendezvous: Rendezvous | None = None
    split_fulfilment: bool = False  # fan out into separate reserve and charge messages
