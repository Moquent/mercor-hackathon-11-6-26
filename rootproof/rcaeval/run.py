"""Asks the subject AI about the prepared RCAEval cases. One fresh request per (case, run).

Run 1 is the official first answer and keeps the original filename; runs 2 and 3 estimate how often the verdict flips.
"""
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..clients import ask_subject
from ..config import ROOT, RCAEVAL_ATTEMPTS, RCAEVAL_TASKS, SUBJECT_MODEL


def attempt_path(case, run_no=1):
    name = f"{case}.json" if run_no == 1 else f"{case}.run{run_no}.json"
    return os.path.join(RCAEVAL_ATTEMPTS, name)


def ask_case(case, run_no=1):
    path = attempt_path(case, run_no)
    if os.path.exists(path):
        return path
    prompt_path = os.path.join(RCAEVAL_TASKS, case, "prompt.txt")
    with open(prompt_path) as f:
        prompt = f.read()
    asked = datetime.now(timezone.utc).isoformat()
    text, raw = ask_subject(prompt)
    record = {"task": case, "run": run_no, "model_requested": SUBJECT_MODEL, "model_reported": raw.get("model"),
              "asked_at_utc": asked, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
              "prompt_file": os.path.relpath(prompt_path, ROOT), "usage": raw.get("usage"), "answer": text}
    if run_no == 1:
        del record["run"]   # the official files predate the run field; keep their shape
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    return path


def ask_all(cases, runs=(1,)):
    os.makedirs(RCAEVAL_ATTEMPTS, exist_ok=True)
    jobs = [(c, r) for r in runs for c in cases]
    with ThreadPoolExecutor(10) as ex:
        for path in ex.map(lambda job: ask_case(*job), jobs):
            print("saved", os.path.relpath(path, ROOT))


def all_cases():
    return sorted(os.listdir(RCAEVAL_TASKS))
