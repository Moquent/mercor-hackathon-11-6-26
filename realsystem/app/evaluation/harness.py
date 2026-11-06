"""Benchmark I/O plus the first-attempt protocol."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.evaluation.model_client import ModelConfig, complete_once, endpoint_host
from app.evaluation.prompts import build_prompt
from app.evaluation.schema import parse_answer
from app.evaluation.scoring import aggregate, grade

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmark"


def slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model)


def load_public_tasks() -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted((BENCH / "tasks").glob("scenario_*.json"))]


def load_truth(task_id: str) -> dict[str, Any]:
    return json.loads((BENCH / "ground_truth" / f"{task_id}.json").read_text())


def load_artifact(scenario_id: str) -> dict[str, Any]:
    return json.loads((BENCH / "runs" / f"{scenario_id}.json").read_text())


def attempt_dir(model: str, condition: str) -> Path:
    return BENCH / "model_attempts" / f"{slug(model)}__{condition}"


def run_attempt(config: ModelConfig, task: dict[str, Any], condition: str) -> dict[str, Any]:
    """One first attempt. An attempt file is never overwritten: the first answer is the answer."""
    out = attempt_dir(config.model, condition) / f"{task['task_id']}.json"
    if out.exists():
        return json.loads(out.read_text())
    prompt = build_prompt(task, condition)
    completion = complete_once(config, prompt)
    parsed, error = parse_answer(completion.raw_text)
    record = {
        "source": "model", "task_id": task["task_id"], "condition": condition, "model_name": config.model,
        "response_model": completion.response_model, "endpoint_host": endpoint_host(config),
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "conversation": "fresh conversation; single user message; no system prompt or history",
        "prompt": prompt, "raw_response": completion.raw_text, "finish_reason": completion.finish_reason,
        "parsed": parsed.model_dump() if parsed else None, "parse_error": error, "token_usage": completion.usage,
        "transport_errors_retried": completion.transport_errors,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1))
    return record


def score_attempt_dir(directory: Path, results_name: str | None = None) -> dict[str, Any]:
    """Grade every attempt in a directory and write per-task results and metrics."""
    tasks = {t["task_id"]: t for t in load_public_tasks()}
    graded, artifacts = [], {}
    meta: dict[str, Any] = {}
    for path in sorted(directory.glob("scenario_*.json")):
        attempt = json.loads(path.read_text())
        meta = {k: attempt.get(k) for k in ("source", "model_name", "condition", "fixture_label")}
        task = tasks[attempt["task_id"]]
        art = artifacts.setdefault(task["scenario_id"], load_artifact(task["scenario_id"]))
        graded.append(grade(attempt, task, load_truth(attempt["task_id"]), art))
    metrics = {**meta, **aggregate(graded), "evaluated_dates_utc": sorted({
        json.loads(p.read_text()).get("evaluated_at_utc", "")[:10] for p in directory.glob("scenario_*.json")})}
    out = BENCH / "results" / (results_name or directory.name)
    out.mkdir(parents=True, exist_ok=True)
    (out / "per_task.json").write_text(json.dumps(graded, indent=1))
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1))
    return metrics
