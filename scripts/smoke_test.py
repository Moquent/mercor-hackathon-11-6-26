#!/usr/bin/env python3
"""End-to-end smoke test against the running stack: one order must create real events, committed
database state, and async message processing, and form a valid DAG. Exits non-zero on failure."""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.config import GATEWAY_URL, INVENTORY_URL, VERIFIER_URL


def main() -> int:
    run_id = f"smoke-{uuid.uuid4().hex[:8]}"
    sku = f"{run_id}-sku"
    httpx.put(f"{INVENTORY_URL}/inventory/{sku}", json={"available": 3}, timeout=5).raise_for_status()
    resp = httpx.post(f"{GATEWAY_URL}/orders", json={"customer_id": "smoke", "run_id": run_id, "label": "smoke",
                                                      "items": [{"sku": sku, "qty": 1}], "amount_cents": 1999}, timeout=10)
    resp.raise_for_status()
    order = resp.json()
    deadline = time.monotonic() + 20
    types: set[str] = set()
    while time.monotonic() < deadline:
        data = httpx.get(f"{VERIFIER_URL}/runs/{run_id}/graph", timeout=5).json()
        types = {e["event_type"] for e in data.get("events", [])}
        if "order_state_committed" in types:
            break
        time.sleep(0.2)
    else:
        print(f"FAILED: order never committed; saw {sorted(types)}")
        return 1
    services = sorted({e["service"] for e in data["events"]})
    commit = next(e for e in data["events"] if e["event_type"] == "order_state_committed")
    verdict = httpx.post(f"{VERIFIER_URL}/verify", json={"run_id": run_id, "claims": [
        {"source_event_id": order["root_event_id"], "target_event_id": commit["event_id"], "relationship": "reachable"}]}, timeout=5).json()["results"][0]
    print(f"run={run_id} events={len(data['events'])} edges={len(data['dependencies'])} services={services}")
    print(f"integrity_valid={data['integrity']['valid']} is_dag={data['is_dag']} verdict={verdict['verdict']} path_len={len(verdict['dependency_path'])}")
    ok = data["integrity"]["valid"] and data["is_dag"] and verdict["verdict"] == "SUPPORTED" and {"gateway", "orders", "inventory", "worker"} <= set(services)
    print("SMOKE TEST " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
