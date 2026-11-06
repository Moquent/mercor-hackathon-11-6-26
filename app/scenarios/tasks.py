"""The 12 benchmark tasks. Questions are authored by hand; every expected answer is computed from
graph structure and persisted state by app.scenarios.ground_truth, never from an LLM."""
from __future__ import annotations

from typing import Any

from app.scenarios.ground_truth import ancestors_of, apply_mask, bfs_path, committing_attempt

REACH = ["reachable", "not_reachable", "insufficient_evidence"]
REACH_DIRECT = ["direct_dependency", "reachable", "not_reachable", "insufficient_evidence"]
PRODUCE = ["produced_commit", "not_produced_commit"]
ANCESTOR = ["shared_ancestor", "no_shared_ancestor"]

# Logical event names used by several specs.
A_ROOT, B_ROOT = "A.request_received", "B.request_received"
S1_FAIL = "A.created.a1.payment_failed"
S1_WARN = "B.created.a1.dependency_warning"
S1_A_COMMIT, S1_B_COMMIT = "A.created.a1.order_state_committed", "B.created.a1.order_state_committed"
S2_COMMIT = "A.created.a2.order_state_committed"
S3_COMMIT = "A.created.a1.order_state_committed"
S4_X, S4_Y = "A.reserve.a1.reservation_failed", "A.charge.a1.payment_failed"


def _slot(source: str, target: str, allowed: list[str], *, ancestor: str | None = None) -> dict[str, Any]:
    return {"source": source, "target": target, "allowed": allowed, "ancestor_mode": ancestor}


TASK_SPECS: list[dict[str, Any]] = [
    # ---- Scenario 1: temporal correlation ------------------------------------------------------
    {"task_id": "scenario_1_task_1", "scenario_id": "scenario_1", "difficulty": "medium",
     "question": "Two customer requests overlapped when a payment failed. Which request is connected to the payment failure through the recorded execution dependencies?",
     "slots": [_slot(A_ROOT, S1_FAIL, REACH), _slot(B_ROOT, S1_FAIL, REACH)],
     "wrong": {1: "reachable"}, "wrong_explanation": "The other request's connection-pool warning immediately preceded the gateway timeout, so that request triggered the payment failure."},
    {"task_id": "scenario_1_task_2", "scenario_id": "scenario_1", "difficulty": "hard",
     "question": "On-call suspects that the connection-pool warning led to the gateway-timeout payment failure. Using the recorded dependencies, evaluate each of the listed event pairs.",
     "slots": [_slot(S1_WARN, S1_FAIL, REACH), _slot("A.order_created", S1_FAIL, REACH), _slot("B.created.a1.payment_requested", S1_FAIL, REACH)],
     "wrong": {0: "reachable"}, "wrong_explanation": "The saturation warning is logged milliseconds before the timeout and names the same gateway, so the warning leads to the failure."},
    {"task_id": "scenario_1_task_3", "scenario_id": "scenario_1", "difficulty": "medium",
     "question": "Determine how far the payment incident's recorded dependencies extend across the two overlapping orders, evaluating each listed pair.",
     "slots": [_slot(S1_WARN, S1_A_COMMIT, REACH), _slot(S1_FAIL, S1_B_COMMIT, REACH), _slot("A.created.a1.payment_requested", S1_A_COMMIT, REACH)],
     "wrong": {1: "reachable"}, "wrong_explanation": "The failed payment happened just before the other order committed on the same worker pool, so the failure reaches that commit."},
    # ---- Scenario 2: retries and duplicates ----------------------------------------------------
    {"task_id": "scenario_2_task_1", "scenario_id": "scenario_2", "difficulty": "medium",
     "question": "A single logical message was delivered several times. Which processing attempt produced the committed state change? Evaluate each delivery against the commit event.",
     "slots": [_slot("A.created.a1.message_delivered", S2_COMMIT, PRODUCE), _slot("A.created.a2.message_delivered", S2_COMMIT, PRODUCE), _slot("A.created.a3.message_delivered", S2_COMMIT, PRODUCE)],
     "wrong": {0: "produced_commit", 1: "not_produced_commit"}, "wrong_explanation": "The first attempt wrote the order row before anything failed, and the retry only finished the job, so the first attempt produced the change."},
    {"task_id": "scenario_2_task_2", "scenario_id": "scenario_2", "difficulty": "hard",
     "question": "The first attempt logged a database write that was rolled back. Establish how the rolled-back transaction relates to the eventual commit: whether it is connected through recorded dependencies, and which attempt's delivery produced the commit.",
     "slots": [_slot("A.created.a1.db_transaction_rolled_back", S2_COMMIT, REACH), _slot("A.created.a1.message_delivered", S2_COMMIT, PRODUCE), _slot("A.created.a2.message_delivered", S2_COMMIT, PRODUCE)],
     "wrong": {0: "not_reachable"}, "wrong_explanation": "A rolled-back transaction leaves no effect, so it cannot be connected to the later commit by any dependency."},
    {"task_id": "scenario_2_task_3", "scenario_id": "scenario_2", "difficulty": "hard",
     "question": "After the commit, the same logical message was delivered once more. Establish the direction of dependency between the commit and this last delivery, and whether the last delivery produced the commit.",
     "slots": [_slot(S2_COMMIT, "A.created.a3.processing_skipped", REACH), _slot("A.created.a3.message_delivered", S2_COMMIT, REACH), _slot("A.created.a3.message_delivered", S2_COMMIT, PRODUCE)],
     "wrong": {0: "not_reachable"}, "wrong_explanation": "The skip is a separate delivery that performs no write, so it has no dependency on the earlier commit."},
    # ---- Scenario 3: partial observability -----------------------------------------------------
    {"task_id": "scenario_3_task_1", "scenario_id": "scenario_3", "difficulty": "medium",
     "mask": {"hide_events": ["A.created.message_published"], "scrub_dangling_refs": False},
     "question": "Can the observed evidence establish whether the order-creation event reached the final order-state commit? Evaluate the listed pair.",
     "slots": [_slot("A.order_created", S3_COMMIT, REACH)],
     "wrong": {0: "reachable"}, "wrong_explanation": "Both events share a trace id and the worker commits the same order, so the creation clearly reached the commit."},
    {"task_id": "scenario_3_task_2", "scenario_id": "scenario_3", "difficulty": "hard",
     "mask": {"hide_edges": [["A.created.message_published", "A.created.a1.message_delivered"]]},
     "question": "Can the observed evidence establish whether the message-publication event reached the final order-state commit? Evaluate the listed pair.",
     "slots": [_slot("A.created.message_published", S3_COMMIT, REACH)],
     "wrong": {0: "not_reachable"}, "wrong_explanation": "The delivery event lists no parent, so the export shows the published message and the commit are disconnected."},
    {"task_id": "scenario_3_task_3", "scenario_id": "scenario_3", "difficulty": "hard",
     "mask": {"hide_events": ["A.created.a1.reservation_committed"], "scrub_dangling_refs": True},
     "question": "Some of the listed relationships can be proven from this export and some cannot. Evaluate each listed pair on what the observed evidence establishes.",
     "slots": [_slot(A_ROOT, "A.created.message_published", REACH), _slot("A.created.a1.payment_requested", S3_COMMIT, REACH),
               _slot("A.created.a1.idempotency_check", S3_COMMIT, REACH), _slot("bg.request_received", S3_COMMIT, REACH)],
     "wrong": {3: "not_reachable"}, "wrong_explanation": "The background request has a different trace id and no visible edge, which proves it is disconnected from the commit."},
    # ---- Scenario 4: shared ancestor -----------------------------------------------------------
    {"task_id": "scenario_4_task_1", "scenario_id": "scenario_4", "difficulty": "medium",
     "question": "Two failures occurred close together on one order: a reservation failure and a payment failure. Did one cause the other in the recorded dependencies, or are both downstream of an earlier shared event? Evaluate both directions, and whether a shared ancestor exists (name one if so).",
     "slots": [_slot(S4_X, S4_Y, REACH), _slot(S4_Y, S4_X, REACH), _slot(S4_X, S4_Y, ANCESTOR, ancestor="any")],
     "wrong": {0: "reachable"}, "wrong_explanation": "The hold was rejected right after the stock check failed, so the reservation failure propagated into the payment failure."},
    {"task_id": "scenario_4_task_2", "scenario_id": "scenario_4", "difficulty": "hard",
     "question": "Identify the nearest shared ancestor of the two failures: the latest event recorded upstream of both, with no other shared ancestor below it. Also evaluate whether the payment failure directly or transitively depends on the reservation failure.",
     "slots": [_slot(S4_X, S4_Y, ANCESTOR, ancestor="nearest"), _slot(S4_X, S4_Y, REACH_DIRECT)],
     "wrong": {1: "direct_dependency"}, "wrong_explanation": "The payment error mentions a rejected hold, which is what a failed reservation produces, so it depends directly on that failure."},
    {"task_id": "scenario_4_task_3", "scenario_id": "scenario_4", "difficulty": "hard",
     "question": "Classify how the order-creation event relates to each failure (direct single-edge dependency or a longer path), and whether the reservation failure reaches the commit that recorded the payment failure.",
     "slots": [_slot("A.order_created", S4_X, REACH_DIRECT), _slot("A.order_created", S4_Y, REACH_DIRECT),
               _slot(S4_X, "A.charge.a1.order_state_committed", REACH_DIRECT), _slot(S4_X, "A.reserve.a1.order_state_committed", REACH_DIRECT)],
     "wrong": {0: "direct_dependency", 2: "reachable"}, "wrong_explanation": "The order fans out directly into both failures, and since the reservation failed first, it flows into the commit that recorded the payment failure too."},
]


def expected_for_slot(slot: dict[str, Any], artifact: dict[str, Any], events: list[dict], deps: list[dict], complete: bool) -> dict[str, Any]:
    """Compute a slot's correct relationship from structure and state (no verifier, no LLM)."""
    names = artifact["manifest"]
    source, target = names[slot["source"]], names[slot["target"]]
    adj: dict[str, list[str]] = {}
    for d in deps:
        adj.setdefault(d["parent_event_id"], []).append(d["child_event_id"])
    allowed = slot["allowed"]
    if allowed == PRODUCE:
        by_id = {e["event_id"]: e for e in events}
        attempt = committing_attempt(artifact, target)
        rel = "produced_commit" if attempt is not None and by_id[source]["delivery_attempt_id"] == attempt else "not_produced_commit"
        return {"relationship": rel, "committing_attempt_id": attempt, "path": bfs_path(adj, source, target) or []}
    if allowed == ANCESTOR:
        shared = ancestors_of(deps, source) & ancestors_of(deps, target)
        nearest = [z for z in shared if not any(z in ancestors_of(deps, other) for other in shared)]
        pool = sorted(nearest) if slot["ancestor_mode"] == "nearest" else sorted(shared)
        return {"relationship": "shared_ancestor" if shared else "no_shared_ancestor", "acceptable_ancestors": pool,
                "nearest_ancestors": sorted(nearest)}
    path = bfs_path(adj, source, target)
    if path:
        return {"relationship": "reachable", "path": path, "hop_count": len(path) - 1}
    return {"relationship": "not_reachable" if complete else "insufficient_evidence", "path": []}


def build_task(spec: dict[str, Any], artifact: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (public task, private ground truth). The prompt may be built only from the public one."""
    mask = spec.get("mask")
    events, deps, hidden = apply_mask(artifact, mask)
    complete = mask is None and artifact["coverage"]["closed_world"]
    names = artifact["manifest"]
    expected, public_slots = [], []
    full_adj: dict[str, list[str]] = {}
    for d in artifact["dependencies"]:
        full_adj.setdefault(d["parent_event_id"], []).append(d["child_event_id"])
    for i, slot in enumerate(spec["slots"]):
        truth = expected_for_slot(slot, artifact, events, deps, complete)
        source, target = names[slot["source"]], names[slot["target"]]
        truth.update(slot_index=i, source=source, target=target, source_name=slot["source"], target_name=slot["target"],
                     allowed=slot["allowed"],
                     reachable_in_complete_private_graph=bfs_path(full_adj, source, target) is not None)
        expected.append(truth)
        public_slots.append({"source_event_id": source, "target_event_id": target, "allowed_relationships": slot["allowed"],
                             "ancestor_required": slot["allowed"] == ANCESTOR})
    public = {
        "task_id": spec["task_id"], "scenario_id": spec["scenario_id"], "question": spec["question"],
        "pairs": public_slots, "coverage": "complete" if complete else "partial", "difficulty": spec["difficulty"],
        "ai_visible_logs": render_logs(events, include_parents=True),
        "ai_visible_logs_no_parents": render_logs(events, include_parents=False),
    }
    evidence_ids = sorted({n for t in expected for n in (t.get("path") or [])} | {t["source"] for t in expected} | {t["target"] for t in expected})
    private = {
        **{k: public[k] for k in ("task_id", "scenario_id", "question", "ai_visible_logs", "coverage", "difficulty")},
        "expected_answer": _expected_text(expected),
        "ground_truth": {"run_id": artifact["run_id"], "slots": expected, "mask": mask, "hidden_event_ids": hidden,
                         "computed_by": "app.scenarios.ground_truth (hand-written BFS + persisted DB state)"},
        "evidence_event_ids": evidence_ids,
    }
    return public, private


def _expected_text(expected: list[dict[str, Any]]) -> str:
    return "; ".join(f"{t['source_name']} -> {t['target_name']}: {t['relationship']}" for t in expected)


def render_logs(events: list[dict[str, Any]], include_parents: bool) -> str:
    """Chronological export. Only whitelisted fields: fault labels and logical names never reach the AI."""
    lines = []
    for e in sorted(events, key=lambda ev: (ev["timestamp"], ev["event_id"])):
        m = e["metadata"]
        parts = [str(e["timestamp"]), f"{m.get('level', 'INFO'):<5}", f"[{e['service']}]", f"event={e['event_id']}",
                 f"type={e['event_type']}", f"status={e['status']}", f"trace={e['trace_id']}"]
        if e["message_id"]:
            parts.append(f"message_id={e['message_id']}")
        if e["delivery_attempt_id"]:
            parts.append(f"delivery_attempt={e['delivery_attempt_id']}")
        if e["database_transaction_id"]:
            parts.append(f"txn={e['database_transaction_id']}")
        if include_parents:
            parts.append("parents=" + (",".join(e["parent_event_ids"]) or "-"))
        parts.append(f'msg="{m.get("message", "")}"')
        lines.append(" ".join(parts))
    return "\n".join(lines)
