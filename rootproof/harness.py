"""Subject-AI harness for the simulated incidents.

Prompts are frozen to tasks/<id>/prompt.txt. Each run is one fresh request whose answer is saved unedited. Run 1 is
the official first attempt and also gets a readable attempts/<id>.md. The verifier never influences a prompt.
"""
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .clients import ask_subject
from .config import ATTEMPTS, ROOT, RUNS, SUBJECT_MODEL, TASKS
from .packets import prompt as render_prompt
from .scenario.incidents import INCIDENTS


def freeze_prompts(incidents=INCIDENTS):
    for inc in incidents:
        os.makedirs(os.path.join(TASKS, inc.id), exist_ok=True)
        with open(os.path.join(TASKS, inc.id, "prompt.txt"), "w") as f:
            f.write(render_prompt(inc))


def _attempt_path(task_id, run_no):
    return os.path.join(ATTEMPTS, f"{task_id}.run{run_no}.json")


def load_attempt(task_id, run_no):
    """The saved record for one run, or None when that run was never asked."""
    path = _attempt_path(task_id, run_no)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def ask_incident(task_id, run_no):
    path = _attempt_path(task_id, run_no)
    if os.path.exists(path):
        return path
    prompt_file = os.path.join(TASKS, task_id, "prompt.txt")
    with open(prompt_file) as f:
        prompt = f.read()
    asked = datetime.now(timezone.utc).isoformat()
    text, raw = ask_subject(prompt)
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    record = {"task": task_id, "run": run_no, "model_requested": SUBJECT_MODEL, "model_reported": raw.get("model"),
              "asked_at_utc": asked, "prompt_sha256": digest,
              "prompt_file": os.path.relpath(prompt_file, ROOT), "usage": raw.get("usage"), "answer": text}
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    if run_no == 1:
        with open(os.path.join(ATTEMPTS, f"{task_id}.md"), "w") as f:
            f.write(f"# {task_id}: first attempt (unedited)\n\n- model: `{record['model_reported']}`\n- asked: {asked}\n"
                    f"- prompt: `{record['prompt_file']}` (sha256 `{digest[:16]}…`)\n\n---\n\n{text}\n")
    return path


def ask_all(runs=max(RUNS)):
    os.makedirs(ATTEMPTS, exist_ok=True)
    freeze_prompts()
    jobs = [(inc.id, r) for r in range(1, runs + 1) for inc in INCIDENTS]
    with ThreadPoolExecutor(8) as ex:
        for path in ex.map(lambda job: ask_incident(*job), jobs):
            print("saved", os.path.relpath(path, ROOT))
