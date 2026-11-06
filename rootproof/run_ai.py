"""Freeze prompts and collect the subject AI's answers. Each call is a fresh, stateless request (= new conversation).

Run 1 of each task is the official first attempt; runs 2-3 only estimate how often the model fails.
Nothing about the verifier or ground truth is ever sent to the model.
"""
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from incidents import INCIDENTS
from llm import ROOT, SUBJECT_MODEL, ask_subject
from render import prompt

TASKS = os.path.join(ROOT, "tasks")
ATTEMPTS = os.path.join(ROOT, "attempts")


def freeze():
    for inc in INCIDENTS:
        d = os.path.join(TASKS, inc["id"])
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "prompt.txt"), "w").write(prompt(inc))


def attempt(task_id, run_no):
    path = os.path.join(ATTEMPTS, f"{task_id}.run{run_no}.json")
    if os.path.exists(path):
        return path
    p = open(os.path.join(TASKS, task_id, "prompt.txt")).read()
    asked = datetime.now(timezone.utc).isoformat()
    text, raw = ask_subject(p)
    rec = {"task": task_id, "run": run_no, "model_requested": SUBJECT_MODEL, "model_reported": raw.get("model"),
           "asked_at_utc": asked, "prompt_sha256": hashlib.sha256(p.encode()).hexdigest(),
           "prompt_file": f"tasks/{task_id}/prompt.txt", "usage": raw.get("usage"), "answer": text}
    json.dump(rec, open(path, "w"), indent=2)
    if run_no == 1:
        md = (f"# {task_id}: first attempt (unedited)\n\n- model: `{rec['model_reported']}`\n- asked: {asked}\n"
              f"- prompt: `{rec['prompt_file']}` (sha256 `{rec['prompt_sha256'][:16]}…`)\n\n---\n\n{text}\n")
        open(os.path.join(ATTEMPTS, f"{task_id}.md"), "w").write(md)
    return path


if __name__ == "__main__":
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    freeze()
    os.makedirs(ATTEMPTS, exist_ok=True)
    jobs = [(i["id"], r) for r in range(1, runs + 1) for i in INCIDENTS]
    with ThreadPoolExecutor(8) as ex:
        for path in ex.map(lambda a: attempt(*a), jobs):
            print("saved", os.path.relpath(path, ROOT))
