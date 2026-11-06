"""Order service: creates orders in PostgreSQL and publishes logical messages to Redis Streams."""
from __future__ import annotations

import json
import uuid

import redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import REDIS_URL, STREAM
from app.database.models import Order
from app.database.session import current_xid, init_db, transaction
from app.instrumentation.context import TraceContext
from app.instrumentation.faults import FaultPlan
from app.instrumentation.recorder import Parent, http_parents, record_event, record_standalone

SERVICE = "orders"
app = FastAPI(title="DAGScope orders")
r = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)


class CreateOrder(BaseModel):
    customer_id: str
    items: list[dict]
    amount_cents: int
    faults: FaultPlan = FaultPlan()


class _InjectedOrderFault(Exception):
    pass


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    r.ping()
    return {"status": "ok"}


def _publish(ctx: TraceContext, order_id: str, kind: str, created: str, body: CreateOrder) -> str:
    """Record the publication, then XADD. The event commits first so a consumer can never see a
    message whose publish event does not exist yet. A failed XADD is recorded as its own child."""
    message_id = str(uuid.uuid4())
    plan = body.faults
    pub = record_standalone(
        ctx, SERVICE, "message_published",
        [Parent(created, "message_published", f"redis-stream:{STREAM}")],
        message=f"publish {kind} order={order_id}",
        message_id=message_id,
        metadata={"order_id": order_id, "message_kind": kind},
    )
    fields = {
        "message_id": message_id, "kind": kind, "order_id": order_id, "trace_id": ctx.trace_id,
        "request_id": ctx.request_id or "", "run_id": ctx.run_id, "label": ctx.label,
        "publish_event_id": pub, "items": json.dumps(body.items), "amount_cents": str(body.amount_cents),
        "faults": plan.model_dump_json(),
    }
    try:
        r.xadd(STREAM, fields)
        if plan.duplicate_publish:
            # Same logical message_id, second stream entry: must not become a second logical operation.
            r.xadd(STREAM, fields)
    except redis.RedisError as exc:
        return record_standalone(
            ctx, SERVICE, "message_publish_failed",
            [Parent(pub, "service_execution", "orders:_publish")],
            status="failed", level="ERROR", message=f"XADD failed: {type(exc).__name__}", message_id=message_id,
        )
    return pub


@app.post("/orders")
def create_order(body: CreateOrder, request: Request) -> JSONResponse:
    ctx = TraceContext.from_headers(request.headers)
    received = record_standalone(ctx, SERVICE, "order_request_received", http_parents(ctx, SERVICE),
                                 message="order request received")
    order_id = str(uuid.uuid4())
    xid: str | None = None
    try:
        with transaction() as s:
            xid = current_xid(s)
            created = record_event(
                s, ctx, SERVICE, "order_created",
                [Parent(received, "service_execution", "orders:create_order")],
                status="committed", xid=xid, message=f"order {order_id} committed",
                metadata={"order_id": order_id},
            )
            s.add(Order(order_id=order_id, trace_id=ctx.trace_id, request_id=ctx.request_id or "",
                        run_id=ctx.run_id, label=ctx.label, customer_id=body.customer_id, items=body.items,
                        amount_cents=body.amount_cents, status="created",
                        fault_plan=body.faults.model_dump(exclude_defaults=True),
                        created_event_id=created, created_xid=xid))
            s.flush()
            if body.faults.fail_order_commit:
                raise _InjectedOrderFault("injected order commit failure")
    except _InjectedOrderFault as exc:
        failed = record_standalone(
            ctx, SERVICE, "db_transaction_rolled_back",
            [Parent(received, "service_execution", "orders:create_order")],
            status="rolled_back", level="ERROR", xid=xid,
            message=f"order insert rolled back: {exc}", metadata={"fault_injected": True},
        )
        return JSONResponse({"order_id": None, "status": "failed", "last_event_id": failed}, status_code=500)

    kinds = ["order.reserve", "order.charge"] if body.faults.split_fulfilment else ["order.created"]
    published = [_publish(ctx, order_id, kind, created, body) for kind in kinds]
    responded = record_standalone(
        ctx, SERVICE, "order_response_sent",
        [Parent(p, "service_execution", "orders:create_order") for p in published],
        message=f"order {order_id} accepted",
    )
    return JSONResponse({"order_id": order_id, "status": "created", "last_event_id": responded})


@app.get("/orders/{order_id}")
def get_order(order_id: str) -> JSONResponse:
    with transaction() as s:
        o = s.get(Order, order_id)
        if o is None:
            return JSONResponse({"detail": "not found"}, status_code=404)
        return JSONResponse({"order_id": o.order_id, "status": o.status, "final_commit_attempt_id": o.final_commit_attempt_id})
