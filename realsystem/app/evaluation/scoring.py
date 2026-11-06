"""Scores attempts against the verifier and the private ground truth; no number here is hand-entered."""
from __future__ import annotations

from typing import Any

from app.scenarios.ground_truth import apply_mask
from app.verifier.claims import verify_claim
from app.verifier.evidence import CoverageMetadata
from app.verifier.graph import build_dependency_dag

ERROR_CATEGORY = {
    "scenario_1": "temporal_correlation", "scenario_2": "retry_attribution",
    "scenario_3": "partial_observability", "scenario_4": "shared_ancestor",
    "rcaeval_reachability": "real_trace_reachability",
}


def task_view(artifact: dict[str, Any], truth: dict[str, Any]):
    """The graph the checker judges a claim against: exactly what the AI was shown, with its coverage."""
    mask = truth["ground_truth"]["mask"]
    events, deps, _ = apply_mask(artifact, mask)
    if mask:
        coverage = CoverageMetadata(closed_world=False, level="partial", source="AI-visible export with hidden events/links",
                                    notes="Incomplete by construction; the private graph is kept for ground truth only.")
    else:
        coverage = CoverageMetadata.model_validate(artifact["coverage"])
    return build_dependency_dag(events, deps, coverage), coverage


def grade(attempt: dict[str, Any], public_task: dict[str, Any], truth: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    slots = truth["ground_truth"]["slots"]
    base = {"task_id": truth["task_id"], "scenario_id": truth["scenario_id"], "coverage": truth["coverage"],
            "category": ERROR_CATEGORY[truth["scenario_id"]], "source": attempt.get("source", "model")}
    if attempt.get("parsed") is None:
        return {**base, "format_failure": True, "parse_error": attempt.get("parse_error"), "task_correct": False,
                "claims": [], "slot_results": [], "mismatch": f"Unparseable output: {attempt.get('parse_error')}"}
    graph, coverage = task_view(artifact, truth)
    full_pairs = {tuple(p) for p in artifact["sql_closure"]}

    claims = []
    for raw in attempt["parsed"]["claims"]:
        result = verify_claim(raw, graph, coverage)
        pair = (raw["source_event_id"], raw["target_event_id"])
        claims.append({"raw": raw, "result": result, "reachable_in_private_graph": pair in full_pairs})

    slot_results, used = [], set()
    for slot in slots:
        match = next((i for i, c in enumerate(claims) if i not in used
                      and c["raw"]["source_event_id"] == slot["source"] and c["raw"]["target_event_id"] == slot["target"]
                      and c["raw"]["relationship"] in slot["allowed"]), None)
        if match is None:
            slot_results.append({"slot_index": slot["slot_index"], "status": "missing", "expected": slot["relationship"], "claimed": None})
            continue
        used.add(match)
        c = claims[match]
        ok = c["raw"]["relationship"] == slot["relationship"] and c["result"]["verdict"] == "SUPPORTED"
        if ok and slot["relationship"] == "shared_ancestor":
            ok = c["raw"].get("ancestor_event_id") in slot["acceptable_ancestors"]
        c["slot_index"] = slot["slot_index"]
        slot_results.append({"slot_index": slot["slot_index"], "status": "correct" if ok else "wrong", "expected": slot["relationship"],
                             "claimed": c["raw"]["relationship"], "verdict": c["result"]["verdict"],
                             "source_name": slot["source_name"], "target_name": slot["target_name"]})
    extras_rejected = [c for i, c in enumerate(claims) if i not in used and c["result"]["verdict"] != "SUPPORTED"]
    correct = all(s["status"] == "correct" for s in slot_results) and not extras_rejected
    wrong = [s for s in slot_results if s["status"] != "correct"]
    mismatch = "" if correct else "; ".join(
        f"{s.get('source_name', '?')} -> {s.get('target_name', '?')}: claimed {s['claimed']}, evidence supports {s['expected']}"
        + (f" (verdict {s['verdict']})" if s.get("verdict") else "") for s in wrong) or "Extra claim rejected by the checker."
    return {**base, "format_failure": False, "task_correct": correct, "claims": claims, "slot_results": slot_results,
            "confidence": attempt["parsed"].get("confidence"), "mismatch": mismatch}


def aggregate(graded: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts and denominators for the report. Slot-level error classes use the verifier's verdict."""
    claims = [c for g in graded for c in g["claims"]]
    verdicts = {v: sum(c["result"]["verdict"] == v for c in claims) for v in ("SUPPORTED", "CONTRADICTED", "NOT_OBSERVED", "INVALID_CLAIM")}
    fp_den = fp = fn_den = fn = cert_den = cert = 0
    for g in graded:
        complete = g["coverage"] == "complete"
        for c in g["claims"]:
            rel, in_full = c["raw"]["relationship"], c["reachable_in_private_graph"]
            if c["result"]["verdict"] == "INVALID_CLAIM":
                continue
            if rel in ("reachable", "direct_dependency"):
                fp_den += 1
                fp += not in_full
            if rel == "not_reachable" and complete:
                fn_den += 1
                fn += in_full
            if not complete and rel in ("reachable", "not_reachable", "direct_dependency"):
                cert_den += 1
                cert += c["result"]["verdict"] == "NOT_OBSERVED"
    categories: dict[str, dict[str, int]] = {}
    for g in graded:
        row = categories.setdefault(g["category"], {"tasks": 0, "correct": 0, "incorrect": 0, "format_failures": 0})
        row["tasks"] += 1
        row["correct"] += g["task_correct"]
        row["incorrect"] += not g["task_correct"] and not g["format_failure"]
        row["format_failures"] += g["format_failure"]
    return {
        "tasks_evaluated": len(graded),
        "tasks_correct": sum(g["task_correct"] for g in graded),
        "format_failures": sum(g["format_failure"] for g in graded),
        "claims_total": len(claims),
        "claims_accepted": verdicts["SUPPORTED"],
        "claims_rejected": verdicts["CONTRADICTED"] + verdicts["INVALID_CLAIM"],
        "claims_inconclusive": verdicts["NOT_OBSERVED"],
        "verdict_counts": verdicts,
        "false_positive_reachability": {"count": fp, "denominator": fp_den, "definition": "reachable/direct claims with no path even in the complete private graph"},
        "false_negative_reachability_complete": {"count": fn, "denominator": fn_den, "definition": "not_reachable claims on complete traces where a path exists"},
        "incorrect_certainty_incomplete": {"count": cert, "denominator": cert_den, "definition": "definite reachability claims on partial traces that the visible evidence does not establish"},
        "error_categories": categories,
    }
