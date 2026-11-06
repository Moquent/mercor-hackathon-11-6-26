"""External track. Runs from committed extracts, so neither the 3.44 GB dataset nor a model is needed."""
import json
from pathlib import Path

import pandas as pd
import pytest

from app.evaluation.scoring import grade
from app.external.rcaeval import failed_mask, naive_error_blame, select_cases, spans_to_records, status_failed
from app.external.rcaeval_bench import ROOT, build_prompt
from app.scenarios.ground_truth import closure
from app.verifier.claims import verify_claim
from app.verifier.evidence import CoverageMetadata
from app.verifier.graph import build_dependency_dag, validate_graph_integrity
from app.verifier.intervention import verify_fault_origin
from app.verifier.reachability import get_descendants

TASKS = [json.loads(p.read_text()) for p in sorted((ROOT / "tasks").glob("*.json"))]
GRAPH_TASKS = [t for t in TASKS if t["family"] != "attribution"]
ATTR_TASKS = [t for t in TASKS if t["family"] == "attribution"]


def truth(task):
    return json.loads((ROOT / "ground_truth" / f"{task['task_id']}.json").read_text())


def extract(task):
    return json.loads((ROOT / "extracts" / f"{task['task_id']}.json").read_text())


def ids(tasks):
    return [t["task_id"] for t in tasks]


def spans_frame(rows):
    cols = ["spanID", "traceID", "serviceName", "operationName", "startTime", "startTimeMillis", "parentSpanID", "duration", "statusCode"]
    return pd.DataFrame(rows, columns=cols).astype({"statusCode": "Int64"})


@pytest.mark.parametrize("code,failed", [(0, False), (None, False), (14, True), (2, True), (200, False), (404, True), (500, True)])
def test_status_codes(code, failed):
    assert status_failed(pd.NA if code is None else code) is failed


def test_adapter_uses_recorded_parent_links_only():
    frame = spans_frame([
        ["a", "t1", "frontend", "root", 1, 0, None, 10, 0],
        ["b", "t1", "checkout", "call", 2, 0, "a", 5, 14],
        ["c", "t2", "frontend", "other", 2, 0, None, 5, 0],  # same instant as b, different trace
        ["d", "t1", "email", "orphan", 3, 0, "never-recorded", 1, 0],
    ])
    events, deps = spans_to_records(frame, "case")
    assert {(d["parent_event_id"], d["child_event_id"]) for d in deps} == {("a", "b"), ("never-recorded", "d")}
    graph = build_dependency_dag(events, deps)
    report = validate_graph_integrity(graph, events)
    assert report["dangling_references"] == [{"parent": "never-recorded", "child": "d"}]
    assert next(e for e in events if e["event_id"] == "b")["status"] == "failed"
    assert failed_mask(frame).sum() == 1


def test_gap_revokes_a_false_completeness_claim():
    frame = spans_frame([["a", "t", "s", "op", 1, 0, None, 1, 0], ["d", "t", "s", "op", 2, 0, "missing", 1, 0]])
    events, deps = spans_to_records(frame, "c")
    graph = build_dependency_dag(events, deps, CoverageMetadata(closed_world=True, level="complete"))
    assert verify_claim({"source_event_id": "a", "target_event_id": "d", "relationship": "reachable"}, graph)["verdict"] == "NOT_OBSERVED"


def test_naive_baseline_blames_the_error_bearing_caller():
    frame = spans_frame([
        ["a", "t", "checkout", "EmailService/Send", 1, 2000, None, 5, 14],
        ["b", "t", "email", "EmailService/Send", 1, 2000, "a", 5, 0],
    ])
    assert naive_error_blame(frame, 1000) == "checkout"


@pytest.mark.parametrize("claimed,injected,expected", [
    ("emailservice", "emailservice", "SUPPORTED"),
    ("checkoutservice", "emailservice", "CONTRADICTED"),
    ("not-a-service", "emailservice", "INVALID_CLAIM"),
    (None, "emailservice", "INVALID_CLAIM"),
])
def test_fault_origin_verdicts(claimed, injected, expected):
    result = verify_fault_origin(claimed, injected, ["checkoutservice", "emailservice"])
    assert result["verdict"] == expected and result["evidence_class"] == "intervention_label"


def test_sample_rule_is_deterministic_and_one_per_service():
    rows = []
    for dataset in ("RE2-OB", "RE2-TT", "RE3-OB", "RE3-TT"):
        for service in ("s1", "s2", "s3"):
            for fault in ("cpu", "mem"):
                for rep in (1, 2):
                    rows.append({"dataset": dataset, "root_cause_service": service, "fault": fault, "repetition": rep, "case": f"{dataset}_{service}_{fault}_{rep}"})
    index = pd.DataFrame(rows)
    picks = select_cases(index)
    assert picks == select_cases(index.sample(frac=1, random_state=3))
    assert all(len(v) == 3 for v in picks.values())
    assert picks["RE2-OB"] == ["RE2-OB_s1_cpu_1", "RE2-OB_s2_mem_1", "RE2-OB_s3_cpu_1"]


def test_sample_manifest_pins_a_revision():
    manifest = json.loads((ROOT / "sample_manifest.json").read_text())
    assert len(manifest["revision"]) == 40 and manifest["licence"] == "MIT"
    assert sum(len(v) for v in manifest["cases"].values()) == 16


def test_task_counts():
    assert len(ATTR_TASKS) == 16 and len(GRAPH_TASKS) >= 5


@pytest.mark.parametrize("task", GRAPH_TASKS, ids=ids(GRAPH_TASKS))
def test_oracles_agree_on_the_extract(task):
    art = extract(task)
    graph = build_dependency_dag(art["events"], art["dependencies"])
    assert {tuple(p) for p in art["sql_closure"]} == closure(art["dependencies"]) == {(n, d) for n in graph for d in get_descendants(n, graph)}


@pytest.mark.parametrize("task", GRAPH_TASKS, ids=ids(GRAPH_TASKS))
def test_answer_key_accepted_and_alternatives_rejected(task):
    t, art = truth(task), extract(task)
    graph = build_dependency_dag(art["events"], art["dependencies"], CoverageMetadata.model_validate(art["coverage"]))
    key = []
    for slot in t["ground_truth"]["slots"]:
        ancestor = (slot.get("acceptable_ancestors") or [None])[0]
        claim = {"source_event_id": slot["source"], "target_event_id": slot["target"], "relationship": slot["relationship"], "ancestor_event_id": ancestor}
        assert verify_claim(claim, graph)["verdict"] == "SUPPORTED", slot
        key.append(claim)
        for other in slot["allowed"]:
            if other != slot["relationship"]:
                wrong = {**claim, "relationship": other}
                assert verify_claim(wrong, graph)["verdict"] != "SUPPORTED", (slot["target_name"], other)
    assert grade({"parsed": {"claims": key, "explanation": "", "confidence": 1.0}}, task, t, art)["task_correct"]


@pytest.mark.parametrize("task", [t for t in GRAPH_TASKS if t["family"] == "gap"], ids=ids([t for t in GRAPH_TASKS if t["family"] == "gap"]))
def test_gap_task_is_a_real_unmasked_gap(task):
    art, t = extract(task), truth(task)
    orphan = next(s for s in t["ground_truth"]["slots"] if s["target_name"] == "orphan_span")
    event = next(e for e in art["events"] if e["event_id"] == orphan["target"])
    assert event["parent_event_ids"] and event["parent_event_ids"][0] not in {e["event_id"] for e in art["events"]}
    assert orphan["relationship"] == "insufficient_evidence" and t["ground_truth"]["mask"] is None


@pytest.mark.parametrize("task", TASKS, ids=ids(TASKS))
def test_prompt_does_not_leak(task):
    t = truth(task)
    prompt = build_prompt(task)
    for forbidden in (t["case"], "root_cause", "expected_answer", "fault_description", "naive", "trace_root", "orphan_span", "failing_span"):
        assert forbidden not in prompt, forbidden
    assert "expected_answer" not in task and "ground_truth" not in task and "case" not in task


@pytest.mark.parametrize("task", ATTR_TASKS, ids=ids(ATTR_TASKS))
def test_attribution_label_is_a_listed_candidate_and_fault_type_is_hidden(task):
    t = truth(task)
    assert t["ground_truth"]["injected_service"] in task["candidates"]
    assert task["candidates"] == sorted(task["candidates"])
    assert len(task["candidates"]) >= 5
    assert f"fault {t['ground_truth']['fault']}" not in build_prompt(task)


def test_real_trace_validation_found_no_violations():
    path = ROOT / "results" / "verifier_on_real_traces.json"
    if not path.exists():
        pytest.skip("run scripts/rcaeval_validate.py first")
    totals = json.loads(path.read_text())["totals"]
    assert totals["oracle_disagreements"] == 0 and totals["false_support"] == 0
    assert totals["wrongly_contradicted"] == 0 and totals["negative_accepted"] == 0
    assert totals["revocations_failed"] == 0 and totals["true_paths_hidden_by_mask"] > 0
