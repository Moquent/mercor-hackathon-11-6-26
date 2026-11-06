"""Asks the subject AI about each prepared RCAEval case. One fresh request per case; first answers are saved unedited."""
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..clients import ask_subject
from ..config import ROOT, RCAEVAL_ATTEMPTS, RCAEVAL_TASKS, SUBJECT_MODEL


def ask_case(case):
    path = os.path.join(RCAEVAL_ATTEMPTS, f"{case}.json")
    if os.path.exists(path):
        return path
    with open(os.path.join(RCAEVAL_TASKS, case, "prompt.txt")) as f:
        prompt = f.read()
    asked = datetime.now(timezone.utc).isoformat()
    text, raw = ask_subject(prompt)
    record = {"task": case, "model_requested": SUBJECT_MODEL, "model_reported": raw.get("model"), "asked_at_utc": asked,
              "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
              "prompt_file": os.path.relpath(os.path.join(RCAEVAL_TASKS, case, "prompt.txt"), ROOT),
              "usage": raw.get("usage"), "answer": text}
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    return path


def ask_all():
    os.makedirs(RCAEVAL_ATTEMPTS, exist_ok=True)
    with ThreadPoolExecutor(10) as ex:
        for path in ex.map(ask_case, sorted(os.listdir(RCAEVAL_TASKS))):
            print("saved", os.path.relpath(path, ROOT))
