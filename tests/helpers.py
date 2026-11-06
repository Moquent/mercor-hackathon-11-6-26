"""Hand-specified graph builders. Expected verdicts in the tests are written by hand, independent of the verifier."""
from __future__ import annotations

from typing import Any

from app.verifier.evidence import CoverageMetadata
from app.verifier.graph import build_dependency_dag

COMPLETE = CoverageMetadata(closed_world=True, level="complete", source="test")
PARTIAL = CoverageMetadata(closed_world=False, level="partial", source="test")


def ev(event_id: str, parents: list[str] | None = None, *, ts: str = "2026-11-06T12:00:00+00:00", **kw: Any) -> dict[str, Any]:
    return {"event_id": event_id, "trace_id": kw.pop("trace_id", "t"), "request_id": None, "service": kw.pop("service", "svc"),
            "event_type": kw.pop("event_type", event_id), "timestamp": ts, "parent_event_ids": parents or [],
            "message_id": kw.pop("message_id", None), "delivery_attempt_id": kw.pop("attempt", None),
            "database_transaction_id": kw.pop("txn", None), "status": kw.pop("status", "ok"), "metadata": kw}


def dep(parent: str, child: str, kind: str = "service_execution") -> dict[str, str]:
    return {"parent_event_id": parent, "child_event_id": child, "dependency_type": kind, "evidence_reference": "test"}


def graph_from(events: list[dict[str, Any]], coverage: CoverageMetadata | None = COMPLETE, kinds: dict[tuple[str, str], str] | None = None):
    kinds = kinds or {}
    deps = [dep(p, e["event_id"], kinds.get((p, e["event_id"]), "service_execution")) for e in events for p in e["parent_event_ids"]]
    return build_dependency_dag(events, deps, coverage)


def claim(source: str, target: str, relationship: str = "reachable", ancestor: str | None = None) -> dict[str, Any]:
    return {"source_event_id": source, "target_event_id": target, "relationship": relationship, "ancestor_event_id": ancestor}


def temporal_trace():
    """Scenario 1 shape: B's warning is 1 ms before A's failure, with no edge between them."""
    return [
        ev("req_a"), ev("order_a", ["req_a"]), ev("pay_req_a", ["order_a"]),
        ev("pay_fail_a", ["pay_req_a"], ts="2026-11-06T12:00:00.002+00:00", status="failed"),
        ev("req_b", trace_id="tb"), ev("order_b", ["req_b"], trace_id="tb"),
        ev("warn_b", ["order_b"], ts="2026-11-06T12:00:00.001+00:00", trace_id="tb", status="warning"),
    ]


def retry_trace():
    """Scenario 2 shape: attempt 1 rolls back, attempt 2 commits, attempt 3 is a skipped duplicate."""
    events = [
        ev("published", message_id="m1"),
        ev("deliver_1", ["published"], message_id="m1", attempt="a1"),
        ev("write_1", ["deliver_1"], message_id="m1", attempt="a1"),
        ev("rollback_1", ["write_1"], message_id="m1", attempt="a1", status="rolled_back", txn="901"),
        ev("failed_1", ["rollback_1"], message_id="m1", attempt="a1", status="failed"),
        ev("deliver_2", ["published", "failed_1"], message_id="m1", attempt="a2"),
        ev("commit_2", ["deliver_2"], message_id="m1", attempt="a2", status="committed", txn="902"),
        ev("deliver_3", ["published"], message_id="m1", attempt="a3"),
        ev("check_3", ["deliver_3", "commit_2"], message_id="m1", attempt="a3", status="duplicate"),
        ev("skip_3", ["check_3"], message_id="m1", attempt="a3", status="skipped"),
    ]
    kinds = {("failed_1", "deliver_2"): "retry_dependency", ("commit_2", "check_3"): "database_commit",
             ("published", "deliver_1"): "message_consumed", ("published", "deliver_2"): "message_consumed",
             ("published", "deliver_3"): "message_consumed"}
    return events, kinds


def shared_trace():
    """Scenario 4 shape: Z fans out to X and Y; neither failure depends on the other."""
    return [ev("root"), ev("z", ["root"]), ev("x_mid", ["z"]), ev("x", ["x_mid"], status="failed"),
            ev("y_mid", ["z"]), ev("y", ["y_mid"], status="failed", ts="2026-11-06T12:00:00.001+00:00"),
            ev("other_root", trace_id="other"), ev("other", ["other_root"], trace_id="other")]
