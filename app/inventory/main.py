"""Inventory service: reservation logic with row-level locking so concurrent requests contend safely."""
from __future__ import annotations

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.database.models import InventoryItem, Reservation
from app.database.session import current_xid, init_db, transaction
from app.instrumentation.context import TraceContext
from app.instrumentation.recorder import Parent, http_parents, record_event, record_standalone

SERVICE = "inventory"
app = FastAPI(title="DAGScope inventory")


class Reserve(BaseModel):
    order_id: str
    sku: str
    qty: int = Field(gt=0)


class Release(BaseModel):
    order_id: str


class Stock(BaseModel):
    available: int = Field(ge=0)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.put("/inventory/{sku}")
def set_stock(sku: str, body: Stock) -> dict:
    with transaction() as s:
        item = s.get(InventoryItem, sku)
        if item is None:
            s.add(InventoryItem(sku=sku, available=body.available))
        else:
            item.available = body.available
    return {"sku": sku, "available": body.available}


@app.get("/inventory/{sku}")
def get_stock(sku: str) -> dict:
    with transaction() as s:
        item = s.get(InventoryItem, sku)
        return {"sku": sku, "available": item.available if item else None}


@app.post("/reservations")
def reserve(body: Reserve, request: Request) -> dict:
    ctx = TraceContext.from_headers(request.headers)
    received = record_standalone(
        ctx, SERVICE, "reservation_requested", http_parents(ctx, SERVICE),
        message=f"reserve sku={body.sku} qty={body.qty}", metadata={"order_id": body.order_id, "sku": body.sku},
    )
    step = Parent(received, "service_execution", "inventory:reserve")
    with transaction() as s:
        xid = current_xid(s)
        # FOR UPDATE serialises contenders; the loser reads the winner's committed write.
        item = s.execute(select(InventoryItem).where(InventoryItem.sku == body.sku).with_for_update()).scalar_one_or_none()
        existing = s.get(Reservation, (body.order_id, body.sku))
        if existing is not None:
            eid = record_event(
                s, ctx, SERVICE, "reservation_reused",
                [step, Parent(existing.commit_event_id, "database_commit", f"pg:reservations:xid={existing.xid}")],
                message=f"reservation already held for order {body.order_id}",
                metadata={"order_id": body.order_id, "sku": body.sku},
            )
            return {"status": "reserved", "event_id": eid}
        if item is None or item.available < body.qty:
            parents = [step]
            if item is not None and item.last_commit_event_id:
                # The read that made this request fail is a recorded data dependency across requests.
                parents.append(Parent(item.last_commit_event_id, "database_commit", f"pg:inventory:sku={body.sku}"))
            eid = record_event(
                s, ctx, SERVICE, "reservation_failed", parents, status="failed", level="ERROR",
                message=f"insufficient stock sku={body.sku} wanted={body.qty} available={item.available if item else 0}",
                metadata={"order_id": body.order_id, "sku": body.sku, "reason": "insufficient_stock"},
            )
            return {"status": "insufficient_stock", "event_id": eid}
        item.available -= body.qty
        eid = record_event(
            s, ctx, SERVICE, "reservation_committed", [step], status="committed", xid=xid,
            message=f"reserved sku={body.sku} qty={body.qty}", metadata={"order_id": body.order_id, "sku": body.sku},
        )
        item.last_commit_event_id = eid
        s.add(Reservation(order_id=body.order_id, sku=body.sku, qty=body.qty, status="active", commit_event_id=eid, xid=xid))
        return {"status": "reserved", "event_id": eid}


@app.post("/reservations/release")
def release(body: Release, request: Request) -> dict:
    ctx = TraceContext.from_headers(request.headers)
    with transaction() as s:
        xid = current_xid(s)
        rows = s.execute(select(Reservation).where(Reservation.order_id == body.order_id, Reservation.status == "active")).scalars().all()
        eid = record_event(
            s, ctx, SERVICE, "reservation_released", http_parents(ctx, SERVICE), status="committed", xid=xid,
            message=f"released {len(rows)} reservation(s) for order {body.order_id}", metadata={"order_id": body.order_id},
        )
        for row in rows:
            item = s.execute(select(InventoryItem).where(InventoryItem.sku == row.sku).with_for_update()).scalar_one()
            item.available += row.qty
            item.last_commit_event_id = eid
            row.status = "released"
        return {"status": "released", "event_id": eid}
