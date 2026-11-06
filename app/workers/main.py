"""Event worker: consumes the Redis stream; each delivery is a separate attempt of a logical message."""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import uuid

import httpx
import redis
from sqlalchemy import case, select, text
from sqlalchemy.dialects.postgresql import insert

from app.config import (CONSUMER_GROUP, HTTP_TIMEOUT_S, INVENTORY_URL, MAX_DELIVERY_ATTEMPTS, REDIS_URL,
                        RETRY_IDLE_MS, STREAM, WORKER_THREADS)
from app.database.models import MessageDelivery, Order, Payment, ProcessedMessage
from app.database.session import SessionLocal, current_xid, init_db, transaction
from app.instrumentation.context import TraceContext
from app.instrumentation.faults import FaultPlan, Rendezvous
from app.instrumentation.recorder import Parent, record_event, record_standalone

SERVICE = "worker"
FAILURE_STATUSES = ("rejected_insufficient_stock", "payment_failed")
log = logging.getLogger("worker")


class RetryableFailure(Exception):
    """Processing failed without a committed effect; the delivery stays un-ACKed."""

    def __init__(self, reason: str, parent_event_id: str):
        super().__init__(reason)
        self.reason = reason
        self.parent_event_id = parent_event_id


class InjectedRollback(Exception):
    pass


class PaymentRaceLost(Exception):
    pass


def begin_delivery(fields: dict[str, str], entry_id: str, consumer: str) -> tuple[TraceContext, str, str]:
    """Create the delivery attempt and its event; link it to the publication and any failed prior attempt."""
    message_id = fields["message_id"]
    attempt_id = str(uuid.uuid4())
    with transaction() as s:
        # Serialise attempt numbering per logical message across consumers.
        s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:m))"), {"m": message_id})
        previous = s.execute(
            select(MessageDelivery).where(MessageDelivery.message_id == message_id)
            .order_by(MessageDelivery.attempt_no.desc()).limit(1)
        ).scalar_one_or_none()
        attempt_no = (previous.attempt_no + 1) if previous else 1
        ctx = TraceContext(
            trace_id=fields["trace_id"], request_id=fields["request_id"] or None, run_id=fields["run_id"],
            label=fields["label"], message_id=message_id, delivery_attempt_id=attempt_id,
            attempt_no=attempt_no, message_kind=fields["kind"],
        )
        parents = [Parent(fields["publish_event_id"], "message_consumed", f"redis-stream:{STREAM}:{entry_id}")]
        if previous is not None and previous.outcome == "failed" and previous.terminal_event_id:
            parents.append(Parent(previous.terminal_event_id, "retry_dependency", f"redelivery-of-attempt:{previous.delivery_attempt_id}"))
        delivered = record_event(
            s, ctx, SERVICE, "message_delivered", parents,
            message=f"delivery attempt {attempt_no} of {fields['kind']} entry={entry_id} consumer={consumer}",
            metadata={"order_id": fields["order_id"], "stream_entry_id": entry_id},
        )
        s.add(MessageDelivery(delivery_attempt_id=attempt_id, message_id=message_id, attempt_no=attempt_no,
                              stream_entry_id=entry_id, consumer=consumer, delivered_event_id=delivered))
    return ctx, attempt_id, delivered


def finish_delivery(attempt_id: str, outcome: str, terminal_event_id: str) -> None:
    with transaction() as s:
        row = s.get(MessageDelivery, attempt_id)
        row.outcome, row.terminal_event_id = outcome, terminal_event_id


def rendezvous(r: redis.Redis, spec: Rendezvous) -> None:
    """Harness-only timing barrier. Bounded: it proceeds on timeout and never changes an outcome."""
    n = r.incr(spec.key)
    r.expire(spec.key, 30)
    release = f"{spec.key}:release"
    if n >= spec.parties:
        r.lpush(release, *["go"] * (spec.parties - 1))
        r.expire(release, 30)
    else:
        r.blpop(release, timeout=spec.timeout_s)


def _call_inventory(ctx: TraceContext, path: str, body: dict, parent: str) -> dict:
    try:
        resp = httpx.post(f"{INVENTORY_URL}{path}", json=body, headers=ctx.headers([parent]), timeout=HTTP_TIMEOUT_S)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise RetryableFailure(f"inventory call failed: {type(exc).__name__}", parent) from exc


def reserve_items(ctx: TraceContext, order_id: str, items: list[dict], parent: str) -> tuple[str, bool]:
    """Returns (last_event_id, all_reserved)."""
    for item in items:
        data = _call_inventory(ctx, "/reservations", {"order_id": order_id, "sku": item["sku"], "qty": item["qty"]}, parent)
        parent = record_standalone(
            ctx, SERVICE, "reservation_result_received",
            [Parent(data["event_id"], "request_propagation", "http-response:inventory.event_id")],
            status="ok" if data["status"] == "reserved" else "failed",
            level="INFO" if data["status"] == "reserved" else "ERROR",
            message=f"inventory answered {data['status']} for sku={item['sku']}", metadata={"order_id": order_id},
        )
        if data["status"] != "reserved":
            return parent, False
    return parent, True


def charge(ctx: TraceContext, r: redis.Redis, order_id: str, amount: int, plan: FaultPlan, parent: str) -> tuple[str, bool]:
    """Simulated payment gateway (labelled simulator; latency and declines come from the fault plan)."""
    with SessionLocal() as s:
        existing = s.get(Payment, order_id)
    step = Parent(parent, "service_execution", "worker:charge")
    if existing is not None:
        eid = record_standalone(
            ctx, SERVICE, "payment_reused",
            [step, Parent(existing.commit_event_id, "database_commit", f"pg:payments:order={order_id}")],
            message="payment already authorized; idempotent reuse", metadata={"order_id": order_id},
        )
        return eid, True
    requested = record_standalone(ctx, SERVICE, "payment_requested", [step],
                                  message=f"authorizing {amount} cents via payment gateway", metadata={"order_id": order_id})
    if plan.rendezvous is not None and ctx.attempt_no == 1:
        rendezvous(r, plan.rendezvous)
    if plan.warning_text:
        # A warning belonging to this request only. It has no edge to any other request's events.
        record_standalone(ctx, SERVICE, "dependency_warning",
                          [Parent(requested, "service_execution", "worker:charge")],
                          status="warning", level="WARN", message=plan.warning_text,
                          metadata={"order_id": order_id, "fault_injected": True})
    if plan.payment_latency_ms:
        time.sleep(plan.payment_latency_ms / 1000)  # simulated gateway latency, not synchronisation
    if plan.payment_failure_reason:
        eid = record_standalone(
            ctx, SERVICE, "payment_failed", [Parent(requested, "service_execution", "worker:charge")],
            status="failed", level="ERROR", message=f"payment failed: {plan.payment_failure_reason}",
            metadata={"order_id": order_id, "reason": plan.payment_failure_reason, "fault_injected": True},
        )
        return eid, False
    try:
        with transaction() as s:
            eid = record_event(s, ctx, SERVICE, "payment_succeeded",
                               [Parent(requested, "service_execution", "worker:charge")],
                               status="committed", xid=current_xid(s), message=f"authorized {amount} cents",
                               metadata={"order_id": order_id})
            inserted = s.execute(
                insert(Payment).values(order_id=order_id, amount_cents=amount, status="authorized", commit_event_id=eid)
                .on_conflict_do_nothing(index_elements=["order_id"]).returning(Payment.order_id)
            ).first()
            if inserted is None:
                raise PaymentRaceLost()  # roll back so no second "committed" payment event exists
    except PaymentRaceLost:
        with SessionLocal() as s:
            winner = s.get(Payment, order_id)
        eid = record_standalone(
            ctx, SERVICE, "payment_reused",
            [Parent(requested, "service_execution", "worker:charge"),
             Parent(winner.commit_event_id, "database_commit", f"pg:payments:order={order_id}")],
            message="concurrent delivery already authorized this payment", metadata={"order_id": order_id},
        )
    return eid, True


def commit_outcome(ctx: TraceContext, order_id: str, new_status: str, outcome: str, parent: str, attempt_id: str,
                   plan: FaultPlan) -> str:
    """The one transaction that changes final state. Its event commits or vanishes with it."""
    step = Parent(parent, "service_execution", "worker:commit_outcome")
    xid: str | None = None
    try:
        with transaction() as s:
            xid = current_xid(s)
            eid = record_event(
                s, ctx, SERVICE, "order_state_committed", [step], status="committed", xid=xid,
                message=f"order {order_id} -> {new_status}", metadata={"order_id": order_id, "new_status": new_status},
            )
            inserted = s.execute(
                insert(ProcessedMessage).values(
                    message_id=ctx.message_id, order_id=order_id, outcome=outcome,
                    delivery_attempt_id=attempt_id, commit_event_id=eid, xid=xid,
                ).on_conflict_do_nothing(index_elements=["message_id"]).returning(ProcessedMessage.message_id)
            ).first()
            if inserted is None:
                # A concurrent delivery of the same logical message won: this one has no effect.
                raise DuplicateLost()
            # A split order commits once per step, so a failed step must not be overwritten by a later success.
            sticky = case((Order.status.in_(FAILURE_STATUSES), Order.status), else_=new_status)
            s.execute(
                Order.__table__.update().where(Order.order_id == order_id).values(
                    status=sticky, final_commit_event_id=eid, final_commit_attempt_id=attempt_id, final_commit_xid=xid,
                )
            )
            if ctx.attempt_no in plan.rollback_attempts:
                raise InjectedRollback()
    except InjectedRollback:
        rolled = record_standalone(
            ctx, SERVICE, "db_transaction_rolled_back", [step], status="rolled_back", level="ERROR", xid=xid,
            message=f"transaction {xid} rolled back while writing order {order_id} -> {new_status}: deadlock detected",
            metadata={"order_id": order_id, "fault_injected": True},
        )
        raise RetryableFailure("final transaction rolled back", rolled)
    return eid


class DuplicateLost(Exception):
    pass


def process(ctx: TraceContext, fields: dict[str, str], attempt_id: str, delivered: str, r: redis.Redis) -> tuple[str, bool]:
    """Returns (terminal_event_id, ack). Raises RetryableFailure to leave the delivery un-ACKed."""
    order_id, kind = fields["order_id"], fields["kind"]
    plan = FaultPlan.model_validate_json(fields["faults"])
    items, amount = json.loads(fields["items"]), int(fields["amount_cents"])
    step = Parent(delivered, "service_execution", "worker:process")

    with SessionLocal() as s:
        done = s.get(ProcessedMessage, ctx.message_id)
    parents = [step]
    if done is not None:
        parents.append(Parent(done.commit_event_id, "database_commit", f"pg:processed_messages:xid={done.xid}"))
    check = record_standalone(
        ctx, SERVICE, "idempotency_check", parents, status="duplicate" if done else "new",
        message=f"message {ctx.message_id} " + ("already committed; skipping" if done else "not yet processed"),
        metadata={"order_id": order_id},
    )
    if done is not None:
        skipped = record_standalone(ctx, SERVICE, "processing_skipped", [Parent(check, "service_execution", "worker:process")],
                                    status="skipped", message="duplicate delivery skipped; no state change",
                                    metadata={"order_id": order_id})
        return skipped, True

    if ctx.attempt_no in plan.transient_fail_attempts:
        raise RetryableFailure("injected transient failure before any write", check)

    last, ok = check, True
    new_status, outcome = "confirmed", "completed"
    if kind in ("order.created", "order.reserve"):
        last, ok = reserve_items(ctx, order_id, items, last)
        if not ok:
            new_status, outcome = "rejected_insufficient_stock", "failed"
    if ok and kind in ("order.created", "order.charge"):
        last, ok = charge(ctx, r, order_id, amount, plan, last)
        if not ok:
            new_status, outcome = "payment_failed", "failed"
            if kind == "order.created":
                data = _call_inventory(ctx, "/reservations/release", {"order_id": order_id}, last)
                last = data["event_id"]

    try:
        commit = commit_outcome(ctx, order_id, new_status, outcome, last, attempt_id, plan)
    except DuplicateLost:
        skipped = record_standalone(ctx, SERVICE, "processing_skipped", [Parent(last, "service_execution", "worker:process")],
                                    status="skipped", message="lost idempotency race; no state change",
                                    metadata={"order_id": order_id})
        return skipped, True
    return commit, ctx.attempt_no not in plan.skip_ack_after_commit_attempts


def handle_entry(r: redis.Redis, entry_id: str, fields: dict[str, str], consumer: str) -> None:
    ctx, attempt_id, delivered = begin_delivery(fields, entry_id, consumer)
    try:
        terminal, ack = process(ctx, fields, attempt_id, delivered, r)
        finish_delivery(attempt_id, "completed", terminal)
    except RetryableFailure as exc:
        failed = record_standalone(ctx, SERVICE, "processing_failed",
                                   [Parent(exc.parent_event_id, "service_execution", "worker:handle_entry")],
                                   status="failed", level="ERROR", message=f"attempt {ctx.attempt_no} failed: {exc.reason}",
                                   metadata={"order_id": fields["order_id"]})
        finish_delivery(attempt_id, "failed", failed)
        ack = False
        if ctx.attempt_no >= MAX_DELIVERY_ATTEMPTS:
            record_standalone(ctx, SERVICE, "message_dead_lettered", [Parent(failed, "service_execution", "worker:handle_entry")],
                              status="failed", level="ERROR", message=f"gave up after {ctx.attempt_no} attempts")
            ack = True
    except Exception as exc:  # an unexpected bug still leaves a recorded failure, never a silent gap in the trace
        log.exception("unexpected processing error")
        failed = record_standalone(ctx, SERVICE, "processing_failed",
                                   [Parent(delivered, "service_execution", "worker:handle_entry")],
                                   status="failed", level="ERROR", message=f"attempt {ctx.attempt_no} failed: unexpected {type(exc).__name__}",
                                   metadata={"order_id": fields["order_id"]})
        finish_delivery(attempt_id, "failed", failed)
        ack = ctx.attempt_no >= MAX_DELIVERY_ATTEMPTS
    if ack:
        r.xack(STREAM, CONSUMER_GROUP, entry_id)


def ensure_group(r: redis.Redis) -> None:
    try:
        r.xgroup_create(STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def consume(consumer: str, stop: threading.Event | None = None) -> None:
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=10, socket_connect_timeout=5)
    while stop is None or not stop.is_set():
        try:
            # Redeliveries first: anything left un-ACKed past the idle threshold.
            claimed = r.xautoclaim(STREAM, CONSUMER_GROUP, consumer, min_idle_time=RETRY_IDLE_MS, count=1)
            entries = claimed[1] if len(claimed) > 1 else []
            if not entries:
                fresh = r.xreadgroup(CONSUMER_GROUP, consumer, {STREAM: ">"}, count=1, block=200)
                entries = fresh[0][1] if fresh else []
            for entry_id, fields in entries:
                if fields:  # xautoclaim can return tombstones for trimmed entries
                    handle_entry(r, entry_id, fields, consumer)
        except redis.RedisError as exc:
            log.warning("redis error, retrying: %s", exc)
            time.sleep(0.5)
        except Exception:  # keep the consumer alive; the entry stays pending and is redelivered
            log.exception("unhandled processing error")
            time.sleep(0.5)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    init_db()
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=10, socket_connect_timeout=5)
    deadline = time.monotonic() + 60
    while True:
        try:
            ensure_group(r)
            break
        except redis.RedisError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)
    base = f"{socket.gethostname()}-{os.getpid()}"
    threads = [threading.Thread(target=consume, args=(f"{base}-{i}",), daemon=True) for i in range(WORKER_THREADS)]
    for t in threads:
        t.start()
    log.info("worker started with %d consumers", len(threads))
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
