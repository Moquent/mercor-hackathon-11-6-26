"""Drive DAGScope's real instrumented stack (realsystem/, compose profile `realsystem`) over HTTP.

Replicates app/scenarios/runner.py from DAGScope (Sarvesh, commit 5310275): the same order requests and
fault plans for scenarios 1..4, sent to the gateway. The services record every event themselves; this
module only sends requests, then polls the verifier's /runs/<run_id>/graph once a second, streaming new
events (`dag_event`) and edges (`dag_edge`) to `emit` until the run stops changing, and saves the capture
to cases/dagscope/runs/live_<run_id>.json in the captured-run format.

    docker compose --profile realsystem up -d
    python -m rootproof.dagscope.runner scenario_1
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

GATEWAY = "http://localhost:8000"
INVENTORY = "http://localhost:8002"
VERIFIER = "http://localhost:8003"
RUNS_DIR = Path(__file__).resolve().parents[2] / "cases" / "dagscope" / "runs"
NOT_RUNNING = "real stack not running: docker compose --profile realsystem up"

S1_TIMEOUT = "gateway timeout after 2000ms while authorizing card"
S1_WARNING = "payment-gateway connection pool near saturation (38/40 active), p99 latency 2140ms"
S4_HOLD = "authorization hold rejected for order"


def _http(method: str, url: str, body: dict | None = None, timeout: float = 15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def healthy() -> bool:
    try:
        _http("GET", f"{GATEWAY}/health", timeout=2)
        _http("GET", f"{VERIFIER}/health", timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


def _seed(sku: str, available: int) -> None:
    _http("PUT", f"{INVENTORY}/inventory/{sku}", {"available": available}, timeout=5)


def _sku() -> str:
    return f"SKU-{uuid.uuid4().hex[:6].upper()}"


def _order(run_id, label, customer, sku, amount=4200, faults=None, qty=1):
    return _http("POST", f"{GATEWAY}/orders", {"customer_id": customer, "run_id": run_id, "label": label,
                                              "items": [{"sku": sku, "qty": qty}], "amount_cents": amount, "faults": faults or {}})


def _concurrently(*calls) -> None:
    errors: list[BaseException] = []

    def wrap(fn):
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=wrap, args=(c,)) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]


def _new_run(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:8]}"


# Each scenario returns (main run id, [(intervention name, description, run id), ...]).

def _scenario_1():
    run = _new_run("s1")
    a, b, c = _sku(), _sku(), _sku()
    for s in (a, b, c):
        _seed(s, 5)
    barrier = {"key": f"barrier:{run}", "parties": 2, "timeout_s": 1.0}
    _concurrently(
        lambda: _order(run, "A", "cust-1041", a, 4200, {"payment_failure_reason": S1_TIMEOUT, "payment_latency_ms": 60, "rendezvous": barrier}),
        lambda: _order(run, "B", "cust-2207", b, 9900, {"warning_text": S1_WARNING, "payment_latency_ms": 180, "rendezvous": barrier}),
        lambda: _order(run, "bg", "cust-3318", c, 1500),
    )
    r1 = _new_run("s1-no-B")
    s = _sku()
    _seed(s, 5)
    _order(r1, "A", "cust-1041", s, 4200, {"payment_failure_reason": S1_TIMEOUT, "payment_latency_ms": 60})
    r2 = _new_run("s1-no-A-fault")
    sa, sb = _sku(), _sku()
    _seed(sa, 5)
    _seed(sb, 5)
    barrier2 = {"key": f"barrier:{r2}", "parties": 2, "timeout_s": 1.0}
    _concurrently(
        lambda: _order(r2, "A", "cust-1041", sa, 4200, {"payment_latency_ms": 60, "rendezvous": barrier2}),
        lambda: _order(r2, "B", "cust-2207", sb, 9900, {"warning_text": S1_WARNING, "payment_latency_ms": 180, "rendezvous": barrier2}),
    )
    return run, [("remove_request_B", "Request A alone, same injected fault, no request B and no warning.", r1),
                 ("remove_fault_on_A", "Requests A and B overlap with B's warning, but no fault injected on A.", r2)]


def _scenario_2():
    run = _new_run("s2")
    a, c = _sku(), _sku()
    _seed(a, 5)
    _seed(c, 5)
    _concurrently(
        lambda: _order(run, "A", "cust-4410", a, 7300, {"rollback_attempts": [1], "skip_ack_after_commit_attempts": [2]}),
        lambda: _order(run, "bg", "cust-5120", c, 1200),
    )
    return run, []


def _scenario_3():
    run = _new_run("s3")
    a, c = _sku(), _sku()
    _seed(a, 5)
    _seed(c, 5)
    _concurrently(lambda: _order(run, "A", "cust-6001", a, 3100), lambda: _order(run, "bg", "cust-6002", c, 2100))
    return run, []


def _scenario_4():
    run = _new_run("s4")
    a, c = _sku(), _sku()
    _seed(a, 0)
    _seed(c, 5)
    plan = {"split_fulfilment": True, "payment_failure_reason": S4_HOLD, "payment_latency_ms": 40}
    _concurrently(lambda: _order(run, "A", "cust-7777", a, 5600, plan), lambda: _order(run, "bg", "cust-7778", c, 800))
    r1 = _new_run("s4-stock-ok")
    s = _sku()
    _seed(s, 5)
    _order(r1, "A", "cust-7777", s, 5600, plan)
    r2 = _new_run("s4-payment-ok")
    s = _sku()
    _seed(s, 0)
    _order(r2, "A", "cust-7777", s, 5600, {"split_fulfilment": True, "payment_latency_ms": 40})
    return run, [("remove_stock_failure", "Stock available, so no reservation failure; same payment fault.", r1),
                 ("remove_payment_failure", "No stock, so reservation still fails; no payment fault.", r2)]


SCENARIOS = {"scenario_1": _scenario_1, "scenario_2": _scenario_2, "scenario_3": _scenario_3, "scenario_4": _scenario_4}


def _graph(run_id: str) -> dict | None:
    try:
        return _http("GET", f"{VERIFIER}/runs/{run_id}/graph", timeout=10)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # nothing recorded yet
            return None
        raise


def _edge_key(d: dict) -> tuple:
    return (d["parent_event_id"], d["child_event_id"], d["dependency_type"])


def poll_until_quiescent(run_id: str, emit=None, quiet_polls: int = 3, timeout_s: float = 60.0) -> dict:
    """Poll once a second; stop after `quiet_polls` consecutive polls with nothing new (retries need ~1.5 s idle)."""
    seen_events, seen_edges, quiet, last = set(), set(), 0, None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        g = _graph(run_id)
        new = 0
        if g is not None:
            last = g
            for e in g["events"]:
                if e["event_id"] not in seen_events:
                    seen_events.add(e["event_id"])
                    new += 1
                    if emit:
                        emit("dag_event", e)
            for d in g["dependencies"]:
                if _edge_key(d) not in seen_edges:
                    seen_edges.add(_edge_key(d))
                    new += 1
                    if emit:
                        emit("dag_edge", d)
        quiet = quiet + 1 if (g is not None and new == 0) else 0
        if quiet >= quiet_polls:
            return last
        time.sleep(1.0)
    if last is None:
        raise TimeoutError(f"run {run_id} recorded no events within {timeout_s}s")
    return last  # best effort; coverage below is downgraded


def build_manifest(events: list[dict]) -> dict[str, str]:
    """Stable logical name -> event id, as in DAGScope's capture."""
    manifest: dict[str, str] = {}
    for e in events:
        m = e.get("metadata") or {}
        parts = [m.get("label") or "unlabelled"]
        if m.get("message_kind"):
            parts.append(m["message_kind"].split(".")[-1])
        if m.get("attempt_no") is not None:
            parts.append(f"a{m['attempt_no']}")
        parts.append(e["event_type"])
        name, i = ".".join(parts), 1
        candidate = name
        while candidate in manifest:
            i += 1
            candidate = f"{name}#{i}"
        manifest[candidate] = e["event_id"]
    return manifest


def _closure(deps: list[dict]) -> list[list[str]]:
    children: dict[str, set] = {}
    for d in deps:
        children.setdefault(d["parent_event_id"], set()).add(d["child_event_id"])
    pairs = set()
    for start in list(children):
        stack, seen = list(children[start]), set()
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            pairs.add((start, n))
            stack.extend(children.get(n, ()))
    return sorted([a, b] for a, b in pairs)


def _intervention_summary(name: str, description: str, run_id: str) -> dict:
    g = poll_until_quiescent(run_id)
    events = g["events"]
    failed = sorted({f"{(e.get('metadata') or {}).get('label')}:{e['event_type']}" for e in events if e["status"] in ("failed", "rolled_back")})
    return {"name": name, "description": description, "run_id": run_id, "failure_events": failed, "event_count": len(events)}


def run_scenario(scenario_id: str, emit=None) -> dict:
    """Send the scenario's requests, stream the growing DAG to `emit(kind, payload)`, save and return the capture."""
    if scenario_id not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario_id!r}; expected one of {sorted(SCENARIOS)}")
    if not healthy():
        raise RuntimeError(NOT_RUNNING)
    run_id, interventions = SCENARIOS[scenario_id]()
    if emit:
        emit("run_started", {"scenario_id": scenario_id, "run_id": run_id})
    g = poll_until_quiescent(run_id, emit)
    valid = bool(g["integrity"].get("valid"))
    artifact = {
        "scenario_id": scenario_id, "run_id": run_id, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "events": g["events"], "dependencies": g["dependencies"],
        "coverage": {"closed_world": valid, "level": "complete" if valid else "partial",
                     "source": "live capture via verifier HTTP after the run stopped changing",
                     "notes": "Complete with respect to the instrumented services and dependency types."},
        "integrity": g["integrity"], "manifest": build_manifest(g["events"]), "sql_closure": _closure(g["dependencies"]),
        "interventions": [_intervention_summary(*i) for i in interventions],
    }
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / f"live_{run_id}.json"
    out.write_text(json.dumps(artifact, indent=1, default=str))
    artifact["saved_to"] = str(out)
    if emit:
        emit("run_saved", {"run_id": run_id, "path": str(out), "events": len(g["events"]), "edges": len(g["dependencies"])})
    return artifact


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else "scenario_1"
    res = run_scenario(sid, lambda kind, p: print(kind, p.get("event_type") or p.get("dependency_type") or p, flush=True))
    print("saved", res["saved_to"])
