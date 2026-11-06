"""API gateway: mints trace/request ids and the root event, dispatches to the order service."""
from __future__ import annotations

import uuid

import httpx
from fastapi import FastAPI, HTTPException

from app.config import HTTP_TIMEOUT_S, ORDERS_URL
from app.instrumentation.context import TraceContext
from app.instrumentation.faults import FaultPlan
from app.instrumentation.recorder import Parent, record_standalone
from pydantic import BaseModel, Field

SERVICE = "gateway"
app = FastAPI(title="DAGScope gateway")


class OrderItem(BaseModel):
    sku: str
    qty: int = Field(gt=0)


class OrderRequest(BaseModel):
    customer_id: str
    items: list[OrderItem] = Field(min_length=1)
    amount_cents: int = Field(ge=0)
    run_id: str = "adhoc"
    label: str = "request"
    faults: FaultPlan = FaultPlan()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/orders", status_code=202)
def create_order(req: OrderRequest) -> dict:
    trace_id, request_id = str(uuid.uuid4()), str(uuid.uuid4())
    ctx = TraceContext(trace_id=trace_id, request_id=request_id, run_id=req.run_id, label=req.label)
    root = record_standalone(
        ctx, SERVICE, "request_received", [], message=f"POST /orders customer={req.customer_id}",
        metadata={"customer_id": req.customer_id},
    )
    dispatched = record_standalone(
        ctx, SERVICE, "order_dispatched",
        [Parent(root, "service_execution", "gateway:create_order")],
        message="dispatching to order service",
    )
    body = {"customer_id": req.customer_id, "items": [i.model_dump() for i in req.items],
            "amount_cents": req.amount_cents, "faults": req.faults.model_dump()}
    try:
        resp = httpx.post(f"{ORDERS_URL}/orders", json=body, headers=ctx.headers([dispatched]), timeout=HTTP_TIMEOUT_S)
    except httpx.HTTPError as exc:
        record_standalone(
            ctx, SERVICE, "response_sent",
            [Parent(dispatched, "service_execution", "gateway:create_order")],
            status="failed", level="ERROR", message=f"order service unreachable: {type(exc).__name__}",
        )
        raise HTTPException(status_code=502, detail="order service unreachable") from exc
    data = resp.json()
    last = data["last_event_id"]
    record_standalone(
        ctx, SERVICE, "response_sent",
        [
            Parent(dispatched, "service_execution", "gateway:create_order"),
            Parent(last, "request_propagation", "http-response:orders.last_event_id"),
        ],
        status="ok" if resp.status_code < 400 else "failed",
        level="INFO" if resp.status_code < 400 else "ERROR",
        message=f"responded {202 if resp.status_code < 400 else resp.status_code}",
    )
    return {**data, "trace_id": trace_id, "request_id": request_id, "root_event_id": root,
            "order_status_code": resp.status_code}
