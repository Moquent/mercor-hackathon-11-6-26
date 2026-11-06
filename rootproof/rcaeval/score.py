"""Scores the verifier and the subject AI on RCAEval cases, against the benchmark labels (never shown to either)."""
import json
import os
import random
import re

from ..config import RCAEVAL_ATTEMPTS, RCAEVAL_TASKS
from .checks import baseline_rank, check, profile
from .run import attempt_path


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


# Instances of the 30 (service, fault) combinations. The rules were designed on dev (2) and tuned while looking at
# instance 1 (test). Instance 3 is fresh: its results are the uncontaminated held-out estimate.
SPLITS = {"dev": "_2", "test": "_1", "fresh": "_3"}
METRICS = ("verifier_accuracy", "ai_service_and_fault_acc", "ai_service_acc", "baseline_ac1", "verifier_flag_recall")
BOOTSTRAP_RESAMPLES = 2000
CI_LEVEL = 0.95


def split_rows(split):
    suffix = SPLITS[split]
    rows = []
    for case in sorted(os.listdir(RCAEVAL_TASKS)):
        attempt_path = os.path.join(RCAEVAL_ATTEMPTS, f"{case}.json")
        if not case.endswith(suffix) or not os.path.exists(attempt_path):
            continue
        features = _read_json(os.path.join(RCAEVAL_TASKS, case, "features.json"))
        rows.append(_case_row(case, features, _read_json(attempt_path)))
    return rows


def bootstrap_ci(rows, seed=0):
    """Percentile bootstrap intervals over cases, resampling with replacement (seeded, so re-runs match)."""
    rng = random.Random(seed)
    draws = {m: [] for m in METRICS}
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [rows[rng.randrange(len(rows))] for _ in rows]
        stats = _summary(sample)
        for m in METRICS:
            if stats[m] is not None:
                draws[m].append(stats[m])
    lo_q, hi_q = (1 - CI_LEVEL) / 2, 1 - (1 - CI_LEVEL) / 2
    out = {}
    for m, xs in draws.items():
        xs.sort()
        out[m] = [round(xs[int(lo_q * len(xs))], 3), round(xs[int(hi_q * len(xs)) - 1], 3)]
    return out


def run_stability(split):
    """Across the runs that exist for each case: the share of cases whose verdict and fault call never change."""
    suffix = SPLITS[split]
    stable, cases, per_run = 0, 0, {}
    for case in sorted(os.listdir(RCAEVAL_TASKS)):
        if not case.endswith(suffix):
            continue
        features = _read_json(os.path.join(RCAEVAL_TASKS, case, "features.json"))
        labels = []
        for run_no in (1, 2, 3):
            path = attempt_path(case, run_no)
            if not os.path.exists(path):
                continue
            row = _case_row(case, features, _read_json(path))
            labels.append((row["verifier"], row["ai_fault_correct"]))
            per_run.setdefault(run_no, []).append(row["ai_fault_correct"])
        if len(labels) > 1:
            cases += 1
            stable += len(set(labels)) == 1
    return {"cases_with_repeats": cases, "stable_share": round(stable / cases, 3) if cases else None,
            "ai_fault_acc_by_run": {str(k): round(sum(v) / len(v), 3) for k, v in sorted(per_run.items())}}


def evaluate(split="test"):
    """split: test (instance 1), dev (instance 2) or fresh (instance 3). Metrics use run 1, the official answer."""
    rows = split_rows(split)
    summary = _summary(rows)
    summary["ci95"] = bootstrap_ci(rows)
    summary["stability"] = run_stability(split)
    return rows, summary
