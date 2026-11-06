#!/usr/bin/env python3
"""Run one model on every benchmark task: first attempts only, one fresh conversation per task.

Reads OPENAI_API_KEY, OPENAI_BASE_URL and MODEL_NAME from the environment or ./.env (.env wins, so
an unrelated exported key can't silently be billed). Attempts already on disk are never regenerated.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from app.evaluation.harness import attempt_dir, load_public_tasks, run_attempt, score_attempt_dir
from app.evaluation.model_client import ModelConfig
from app.evaluation.prompts import CONDITIONS
from app.evaluation.report import write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", choices=list(CONDITIONS), default="explicit_parents")
    parser.add_argument("--only", nargs="*", help="task ids")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
    config = ModelConfig.from_env()
    if config is None:
        print("No model configured: set OPENAI_API_KEY and MODEL_NAME (and OPENAI_BASE_URL if not OpenAI).\n"
              "No AI attempts were made and no scores were produced.")
        return 2
    tasks = [t for t in load_public_tasks() if not args.only or t["task_id"] in args.only]
    for task in tasks:
        record = run_attempt(config, task, args.condition)
        print(f"{task['task_id']}: parsed={record['parsed'] is not None} finish={record['finish_reason']} usage={(record['token_usage'] or {}).get('total_tokens')}")
    metrics = score_attempt_dir(attempt_dir(config.model, args.condition))
    print(f"tasks_correct={metrics['tasks_correct']}/{metrics['tasks_evaluated']} verdicts={metrics['verdict_counts']}")
    print(f"report: {write_report()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
