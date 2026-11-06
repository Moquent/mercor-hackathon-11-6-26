"""Scores the verifier and the subject AI on RCAEval cases, against the benchmark labels (never shown to either)."""
import json
import os
import re

from ..config import RCAEVAL_ATTEMPTS, RCAEVAL_TASKS
from .checks import baseline_rank, check, profile


def parse(answer):
    matches = re.findall(r"```json\s*(\{.*?\})\s*```", answer, re.S)
    try:
        return json.loads(matches[-1]) if matches else {}
    except json.JSONDecodeError:
        return {}


def _read_json(path):
    with open(path) as f:
        return json.load(f)


def _case_row(case, features, attempt):
    claim = parse(attempt["answer"])
    checks, svc, path = check(features, claim)
    prof = profile(features)
    rank = baseline_rank(prof)
    fault = str(claim.get("fault_type", "")).lower()
    svc_ok = svc == features["truth_service"]
    fault_ok = svc_ok and fault == features["truth_fault"]
    passed = all(c.passed is not False for c in checks)
    return {"case": case, "truth_service": features["truth_service"], "truth_fault": features["truth_fault"],
            "ai_service": svc, "ai_fault": fault, "ai_path": claim.get("propagation_path"),
            "ai_service_correct": svc_ok, "ai_fault_correct": fault_ok,
            "verifier": "PASS" if passed else "FLAG",
            "checks": [c.to_dict() for c in checks],
            "baseline_top3": rank[:3],
            "model": attempt["model_reported"], "asked_at_utc": attempt["asked_at_utc"]}


def _summary(rows):
    n = len(rows)
    wrong = [r for r in rows if not r["ai_fault_correct"]]
    flagged = [r for r in rows if r["verifier"] == "FLAG"]
    tp = sum(1 for r in flagged if not r["ai_fault_correct"])
    passed = [r for r in rows if r["verifier"] == "PASS"]
    return {
        "cases": n,
        "ai_service_acc": sum(r["ai_service_correct"] for r in rows) / n,
        "ai_service_and_fault_acc": sum(r["ai_fault_correct"] for r in rows) / n,
        "baseline_ac1": sum(r["baseline_top3"][0] == r["truth_service"] for r in rows) / n,
        "baseline_ac3": sum(r["truth_service"] in r["baseline_top3"] for r in rows) / n,
        "verifier_flag_precision": tp / len(flagged) if flagged else None,
        "verifier_flag_recall": tp / len(wrong) if wrong else None,
        "verifier_accuracy": sum((r["verifier"] == "FLAG") == (not r["ai_fault_correct"]) for r in rows) / n,
        "pass_precision": sum(r["ai_fault_correct"] for r in passed) / max(1, len(passed)),
        "flagged": len(flagged), "ai_wrong": len(wrong),
    }


def evaluate(split="test"):
    """test = instance 1 (held out); dev = instance 2 (used to design the signature rules)."""
    suffix = "_1" if split == "test" else "_2"
    rows = []
    for case in sorted(os.listdir(RCAEVAL_TASKS)):
        attempt_path = os.path.join(RCAEVAL_ATTEMPTS, f"{case}.json")
        if not case.endswith(suffix) or not os.path.exists(attempt_path):
            continue
        features = _read_json(os.path.join(RCAEVAL_TASKS, case, "features.json"))
        rows.append(_case_row(case, features, _read_json(attempt_path)))
    return rows, _summary(rows)
