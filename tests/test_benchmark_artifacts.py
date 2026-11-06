"""Checks over the captured benchmark: runs without Docker, from committed real traces."""
import json

import pytest

from app.evaluation.harness import load_artifact, load_public_tasks, load_truth
from app.evaluation.prompts import build_prompt
from app.evaluation.scoring import grade, task_view
from app.scenarios.ground_truth import closure
from app.scenarios.tasks import TASK_SPECS
from app.verifier.claims import verify_claim
from app.verifier.graph import build_dependency_dag, validate_graph_integrity
from app.verifier.reachability import get_descendants

SCENARIOS = ["scenario_1", "scenario_2", "scenario_3", "scenario_4"]
TASK_IDS = [s["task_id"] for s in TASK_SPECS]
SPEC = {s["task_id"]: s for s in TASK_SPECS}


def expected_claims(truth):
    claims = []
    for slot in truth["ground_truth"]["slots"]:
        ancestor = (slot.get("acceptable_ancestors") or [None])[0] if slot["relationship"] == "shared_ancestor" else None
        claims.append({"source_event_id": slot["source"], "target_event_id": slot["target"], "relationship": slot["relationship"], "ancestor_event_id": ancestor})
    return claims


def test_twelve_tasks_three_per_scenario():
    tasks = load_public_tasks()
    assert len(tasks) == 12
    for scenario in SCENARIOS:
        assert sum(t["scenario_id"] == scenario for t in tasks) == 3


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_captured_trace_is_a_valid_dag_from_real_services(scenario):
    art = load_artifact(scenario)
    graph = build_dependency_dag(art["events"], art["dependencies"])
    assert validate_graph_integrity(graph, art["events"])["valid"]
    assert {"gateway", "orders", "inventory", "worker"} <= {e["service"] for e in art["events"]}


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_three_independent_reachability_oracles_agree(scenario):
    art = load_artifact(scenario)
    graph = build_dependency_dag(art["events"], art["dependencies"])
    nx_pairs = {(n, d) for n in graph for d in get_descendants(n, graph)}
    assert {tuple(p) for p in art["sql_closure"]} == closure(art["dependencies"]) == nx_pairs


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_expected_answer_is_accepted_by_the_checker(task_id):
    truth = load_truth(task_id)
    graph, coverage = task_view(load_artifact(truth["scenario_id"]), truth)
    for c in expected_claims(truth):
        assert verify_claim(c, graph, coverage)["verdict"] == "SUPPORTED", c


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_plausible_wrong_answer_is_not_accepted(task_id):
    truth = load_truth(task_id)
    graph, coverage = task_view(load_artifact(truth["scenario_id"]), truth)
    slots = truth["ground_truth"]["slots"]
    for index, relationship in SPEC[task_id]["wrong"].items():
        slot = slots[index]
        assert relationship != slot["relationship"]
        result = verify_claim({"source_event_id": slot["source"], "target_event_id": slot["target"], "relationship": relationship}, graph, coverage)
        assert result["verdict"] in ("CONTRADICTED", "NOT_OBSERVED"), (slot, result["verdict"])


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_every_wrong_alternative_for_every_slot_is_not_accepted(task_id):
    """Exhaustive: all other allowed relationships for each pair must fail, not just the hand-picked trap."""
    truth = load_truth(task_id)
    graph, coverage = task_view(load_artifact(truth["scenario_id"]), truth)
    for slot in truth["ground_truth"]["slots"]:
        for relationship in slot["allowed"]:
            if relationship == slot["relationship"]:
                continue
            verdict = verify_claim({"source_event_id": slot["source"], "target_event_id": slot["target"], "relationship": relationship}, graph, coverage)["verdict"]
            assert verdict != "SUPPORTED", (slot["source_name"], slot["target_name"], relationship)


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_prompt_never_leaks_the_answer_or_labels(task_id):
    public = next(t for t in load_public_tasks() if t["task_id"] == task_id)
    truth = load_truth(task_id)
    prompt = build_prompt(public)
    for slot in truth["ground_truth"]["slots"]:
        assert slot["source_name"] not in prompt and slot["target_name"] not in prompt
    for forbidden in ("fault_injected", "fault_plan", "expected_answer", "ground_truth", "run_id", truth["ground_truth"]["run_id"], "payment_failure_reason"):
        assert forbidden not in prompt
    assert "expected_answer" not in public and "ground_truth" not in public and "evidence_event_ids" not in public


@pytest.mark.parametrize("task_id", [t for t in TASK_IDS if t.startswith("scenario_3")])
def test_hidden_events_are_absent_from_the_public_task(task_id):
    public = next(t for t in load_public_tasks() if t["task_id"] == task_id)
    truth = load_truth(task_id)
    assert public["coverage"] == "partial"
    mask = truth["ground_truth"]["mask"]
    for hidden in truth["ground_truth"]["hidden_event_ids"]:
        assert f"event={hidden}" not in public["ai_visible_logs"]
        assert hidden not in json.dumps(public["pairs"])
    if mask.get("scrub_dangling_refs", True):
        for hidden in truth["ground_truth"]["hidden_event_ids"]:
            assert hidden not in public["ai_visible_logs"]


def test_partial_task_truth_differs_from_private_graph():
    """The whole point of scenario 3: the hidden path exists privately but can't be proven publicly."""
    slot = load_truth("scenario_3_task_1")["ground_truth"]["slots"][0]
    assert slot["relationship"] == "insufficient_evidence" and slot["reachable_in_complete_private_graph"] is True


def test_scenario_1_warning_is_close_in_time_but_disconnected():
    from datetime import datetime
    art = load_artifact("scenario_1")
    names, by_id = art["manifest"], {e["event_id"]: e for e in art["events"]}
    warn, fail = names["B.created.a1.dependency_warning"], names["A.created.a1.payment_failed"]
    gap = abs((datetime.fromisoformat(by_id[fail]["timestamp"]) - datetime.fromisoformat(by_id[warn]["timestamp"])).total_seconds())
    assert gap < 0.5, gap
    assert (warn, fail) not in {tuple(p) for p in art["sql_closure"]}


def test_scenario_2_db_state_agrees_with_the_trace():
    art = load_artifact("scenario_2")
    names, by_id = art["manifest"], {e["event_id"]: e for e in art["events"]}
    commit = by_id[names["A.created.a2.order_state_committed"]]
    rollback = by_id[names["A.created.a1.db_transaction_rolled_back"]]
    order = next(o for o in art["state"]["orders"] if o["label"] == "A")
    assert order["status"] == "confirmed" and order["final_commit_event_id"] == commit["event_id"]
    assert order["final_commit_attempt_id"] == commit["delivery_attempt_id"]
    assert art["state"]["xact_status"][commit["database_transaction_id"]] == "committed"
    assert art["state"]["xact_status"][rollback["database_transaction_id"]] == "aborted"
    assert rollback["status"] == "rolled_back"


def test_scenario_2_one_logical_message_three_attempts_one_commit():
    art = load_artifact("scenario_2")
    a_msg = {e["message_id"] for e in art["events"] if e["metadata"]["label"] == "A" and e["message_id"]}
    assert len(a_msg) == 1
    attempts = [d for d in art["state"]["message_deliveries"] if d["message_id"] in a_msg]
    assert [d["attempt_no"] for d in attempts] == [1, 2, 3]
    assert [d["outcome"] for d in attempts] == ["failed", "completed", "completed"]
    assert sum(p["message_id"] in a_msg for p in art["state"]["processed_messages"]) == 1
    committed = [e for e in art["events"] if e["event_type"] == "order_state_committed" and e["message_id"] in a_msg]
    assert len(committed) == 1


def test_no_rolled_back_transaction_appears_as_a_commit_in_any_run():
    for scenario in SCENARIOS:
        art = load_artifact(scenario)
        for e in art["events"]:
            if e["database_transaction_id"]:
                status = art["state"]["xact_status"][e["database_transaction_id"]]
                if e["status"] == "committed":
                    assert status == "committed", e["event_id"]
                if e["status"] == "rolled_back":
                    assert status == "aborted", e["event_id"]


@pytest.mark.parametrize("scenario,removed,persisting", [
    ("scenario_1", "remove_request_B", "A:payment_failed"),
    ("scenario_4", "remove_stock_failure", "A:payment_failed"),
    ("scenario_4", "remove_payment_failure", "A:reservation_failed"),
])
def test_interventions_show_the_failure_persists_without_the_suspect(scenario, removed, persisting):
    run = next(i for i in load_artifact(scenario)["interventions"] if i["name"] == removed)
    assert persisting in run["failure_events"]


def test_intervention_removing_the_fault_removes_the_failure():
    run = next(i for i in load_artifact("scenario_1")["interventions"] if i["name"] == "remove_fault_on_A")
    assert run["failure_events"] == [] and run["order_outcomes"]["A"] == "confirmed"


def test_failed_split_step_stays_failed_after_later_success():
    run = next(i for i in load_artifact("scenario_4")["interventions"] if i["name"] == "remove_payment_failure")
    assert run["order_outcomes"]["A"] == "rejected_insufficient_stock"


def test_answer_key_grades_full_marks_and_trap_answer_fails():
    for task_id in TASK_IDS:
        truth = load_truth(task_id)
        art = load_artifact(truth["scenario_id"])
        public = next(t for t in load_public_tasks() if t["task_id"] == task_id)
        good = {"parsed": {"claims": expected_claims(truth), "explanation": "", "confidence": 0.9}}
        assert grade(good, public, truth, art)["task_correct"], task_id
        bad_claims = expected_claims(truth)
        for index, relationship in SPEC[task_id]["wrong"].items():
            bad_claims[index]["relationship"] = relationship
        bad = {"parsed": {"claims": bad_claims, "explanation": "", "confidence": 0.9}}
        assert not grade(bad, public, truth, art)["task_correct"], task_id


