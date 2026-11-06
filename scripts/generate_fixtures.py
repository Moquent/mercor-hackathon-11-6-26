#!/usr/bin/env python3
"""Write HANDCRAFTED attempts for exercising the checker and the dashboard without a model.
These are NOT AI responses and are labelled as such in every file and every screen they reach."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.harness import BENCH, score_attempt_dir
from app.evaluation.schema import parse_answer
from app.scenarios.tasks import TASK_SPECS

LABEL = "HANDCRAFTED TEST FIXTURE - NOT A MODEL RESPONSE"


def write(variant: str, spec: dict, claims: list[dict], explanation: str) -> Path:
    directory = BENCH / "model_attempts" / f"HANDCRAFTED_{variant}__explicit_parents"
    directory.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({"claims": claims, "explanation": explanation, "confidence": 0.9})
    parsed, error = parse_answer(raw)
    (directory / f"{spec['task_id']}.json").write_text(json.dumps({
        "source": "handcrafted", "fixture_label": LABEL, "task_id": spec["task_id"], "condition": "explicit_parents",
        "model_name": f"HANDCRAFTED_{variant}", "prompt": None, "raw_response": raw, "parsed": parsed.model_dump() if parsed else None,
        "parse_error": error, "evaluated_at_utc": "",
    }, indent=1))
    return directory


def main() -> None:
    directories = set()
    for spec in TASK_SPECS:
        truth = json.loads((BENCH / "ground_truth" / f"{spec['task_id']}.json").read_text())
        correct = [{"source_event_id": s["source"], "target_event_id": s["target"], "relationship": s["relationship"],
                    "ancestor_event_id": (s.get("acceptable_ancestors") or [None])[0] if s["relationship"] == "shared_ancestor" else None}
                   for s in truth["ground_truth"]["slots"]]
        directories.add(write("correct", spec, correct, "Handcrafted answer key derived from the private ground truth."))
        wrong = [dict(c) for c in correct]
        for index, relationship in spec["wrong"].items():
            wrong[index]["relationship"] = relationship
        directories.add(write("plausible_wrong", spec, wrong, spec["wrong_explanation"]))
    for directory in sorted(directories):
        metrics = score_attempt_dir(directory)
        print(f"{directory.name}: tasks_correct={metrics['tasks_correct']}/{metrics['tasks_evaluated']} verdicts={metrics['verdict_counts']}")


if __name__ == "__main__":
    main()
