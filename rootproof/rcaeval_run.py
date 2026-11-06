"""Ask the subject AI about each prepared RCAEval case (one fresh request each); first answers saved unedited."""
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from llm import ROOT, SUBJECT_MODEL, ask_subject

TASKS = os.path.join(ROOT, "tasks", "rcaeval")
OUT = os.path.join(ROOT, "attempts", "rcaeval")


def attempt(case):
    path = os.path.join(OUT, f"{case}.json")
    if os.path.exists(path):
        return path
    p = open(os.path.join(TASKS, case, "prompt.txt")).read()
    asked = datetime.now(timezone.utc).isoformat()
    text, raw = ask_subject(p)
    json.dump({"task": case, "model_requested": SUBJECT_MODEL, "model_reported": raw.get("model"), "asked_at_utc": asked,
               "prompt_sha256": hashlib.sha256(p.encode()).hexdigest(), "prompt_file": f"tasks/rcaeval/{case}/prompt.txt",
               "usage": raw.get("usage"), "answer": text}, open(path, "w"), indent=2)
    return path


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    with ThreadPoolExecutor(10) as ex:
        for p in ex.map(attempt, sorted(os.listdir(TASKS))):
            print("saved", os.path.relpath(p, ROOT))
