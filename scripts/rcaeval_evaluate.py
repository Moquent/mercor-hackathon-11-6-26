#!/usr/bin/env python3
"""First attempts on the external RCAEval tasks; same model and protocol as the primary benchmark."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from app.evaluation.harness import slug
from app.evaluation.model_client import ModelConfig, complete_once, endpoint_host
from app.evaluation.report import write_report
from app.evaluation.schema import ModelAnswer, parse_answer
from app.evaluation.scoring import aggregate, grade
from app.external.rcaeval_bench import ROOT, FaultAnswer, build_prompt
from app.verifier.intervention import verify_fault_origin


def load_tasks() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((ROOT / "tasks").glob("*.json"))]


def attempt(config: ModelConfig, task: dict) -> dict:
    out = ROOT / "attempts" / slug(config.model) / f"{task['task_id']}.json"
    if out.exists():
        return json.loads(out.read_text())
    prompt = build_prompt(task)
    done = complete_once(config, prompt)
    parsed, error = parse_answer(done.raw_text, FaultAnswer if task["family"] == "attribution" else ModelAnswer)
    record = {"source": "model", "task_id": task["task_id"], "family": task["family"], "model_name": config.model, "response_model": done.response_model,
              "endpoint_host": endpoint_host(config), "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
              "conversation": "fresh conversation; single user message; no system prompt or history", "prompt": prompt, "raw_response": done.raw_text,
              "finish_reason": done.finish_reason, "parsed": parsed.model_dump() if parsed else None, "parse_error": error, "token_usage": done.usage,
              "transport_errors_retried": done.transport_errors}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1))
    return record


def score(model: str, tasks: list[dict]) -> dict:
    graded, attributions, misses = [], [], []
    directory = ROOT / "attempts" / slug(model)
    for task in tasks:
        path = directory / f"{task['task_id']}.json"
        if not path.exists():
            continue
        record = json.loads(path.read_text())
        truth = json.loads((ROOT / "ground_truth" / f"{task['task_id']}.json").read_text())
        if task["family"] == "attribution":
            gt = truth["ground_truth"]
            parsed = record["parsed"]
            result = verify_fault_origin(parsed["injected_fault_service"], gt["injected_service"], task["candidates"]) if parsed else None
            row = {"task_id": task["task_id"], "case": truth["case"], "family": "attribution", "format_failure": parsed is None, "claimed": parsed["injected_fault_service"] if parsed else None,
                   "confidence": parsed["confidence"] if parsed else None, "injected_service": gt["injected_service"], "fault": gt["fault"], "verdict": result["verdict"] if result else None,
                   "result": result, "service_present_in_traces": gt["service_present_in_traces"], "naive_baseline": gt["naive_error_blame_baseline"], "naive_correct": gt["naive_error_blame_baseline"] == gt["injected_service"],
                   "task_correct": bool(result and result["verdict"] == "SUPPORTED")}
            attributions.append(row)
            if not row["task_correct"]:
                misses.append({"task_id": task["task_id"], "detail": f"claimed {row['claimed']}, injected {row['injected_service']} (fault {row['fault']}, stated confidence {row['confidence']})"})
        else:
            artifact = json.loads((ROOT / "extracts" / f"{task['task_id']}.json").read_text())
            row = {**grade(record, task, truth, artifact), "family": task["family"], "case": truth["case"]}
            graded.append(row)
            if not row["task_correct"]:
                misses.append({"task_id": task["task_id"], "detail": row["mismatch"]})
    wrong_naive = [a for a in attributions if not a["naive_correct"]]
    absent = [a for a in attributions if not a["service_present_in_traces"]]
    metrics = {"model_name": model, "evaluated_dates_utc": sorted({json.loads(p.read_text())["evaluated_at_utc"][:10] for p in directory.glob("*.json")}),
               "attribution": {"tasks": len(attributions), "correct": sum(a["task_correct"] for a in attributions), "format_failures": sum(a["format_failure"] for a in attributions),
                               "naive_correct": sum(a["naive_correct"] for a in attributions), "naive_wrong": len(wrong_naive), "correct_when_naive_wrong": sum(a["task_correct"] for a in wrong_naive),
                               "service_absent": len(absent), "correct_service_absent": sum(a["task_correct"] for a in absent)},
               "reachability": aggregate(graded), "misses": misses}
    out = ROOT / "results" / slug(model)
    out.mkdir(parents=True, exist_ok=True)
    (out / "per_task.json").write_text(json.dumps(graded + attributions, indent=1))
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1))
    return metrics


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
    config = ModelConfig.from_env()
    if config is None:
        print("No model configured; no attempts were made.")
        return 2
    tasks = load_tasks()
    for task in tasks:
        record = attempt(config, task)
        print(f"{task['task_id']}: parsed={record['parsed'] is not None} finish={record['finish_reason']} tokens={(record['token_usage'] or {}).get('total_tokens')}", flush=True)
    metrics = score(config.model, tasks)
    print(json.dumps({k: metrics[k] for k in ("attribution", "misses")}, indent=1))
    print(json.dumps({k: metrics["reachability"][k] for k in ("tasks_evaluated", "tasks_correct", "verdict_counts", "incorrect_certainty_incomplete")}))
    write_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
