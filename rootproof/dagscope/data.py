"""Read access to the DAGScope benchmark copied under cases/dagscope/ (runs, tasks, ground truth, attempts, results)."""
import json
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "cases", "dagscope"))
_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")
_cache = {}


def _path(*parts):
    for p in parts:
        if not _SAFE.match(p):
            raise ValueError(f"bad name {p!r}")
    return os.path.join(ROOT, *parts)


def _load(path):
    st = os.stat(path)
    hit = _cache.get(path)
    if hit and hit[0] == st.st_mtime:
        return hit[1]
    with open(path) as f:
        obj = json.load(f)
    _cache[path] = (st.st_mtime, obj)
    return obj


def _names(sub):
    d = os.path.join(ROOT, sub)
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []


def list_runs():
    out = []
    for sid in _names("runs"):
        a = load_artifact(sid)
        out.append({"scenario_id": sid, "scenario": a.get("scenario_id"), "run_id": a["run_id"], "captured_at_utc": a.get("captured_at_utc"),
                    "n_events": len(a["events"]), "n_dependencies": len(a["dependencies"]),
                    "services": sorted({e["service"] for e in a["events"]}), "coverage": a["coverage"]})
    return out


def load_artifact(scenario_id):
    """The full captured run as saved by DAGScope."""
    return _load(_path("runs", scenario_id + ".json"))


def load_run(scenario_id):
    """What the page needs: events, dependencies, coverage, manifest (logical name -> event id), integrity."""
    a = load_artifact(scenario_id)
    return {k: a.get(k) for k in ("scenario_id", "run_id", "captured_at_utc", "events", "dependencies", "coverage",
                                  "manifest", "integrity", "interventions")}


def list_tasks():
    out = []
    for tid in _names("tasks"):
        t = load_public_task(tid)
        out.append({"task_id": tid, "scenario_id": t["scenario_id"], "question": t["question"], "coverage": t["coverage"],
                    "difficulty": t.get("difficulty"), "n_pairs": len(t["pairs"]),
                    "attempts": [m for m in attempt_sets() if os.path.isfile(os.path.join(ROOT, "model_attempts", m, tid + ".json"))]})
    return out


def load_public_task(task_id):
    return _load(_path("tasks", task_id + ".json"))


def load_truth(task_id):
    return _load(_path("ground_truth", task_id + ".json"))


def attempt_sets():
    d = os.path.join(ROOT, "model_attempts")
    return sorted(x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))) if os.path.isdir(d) else []


def load_attempts(task_id):
    """{attempt_set: attempt record} for every set that has this task."""
    out = {}
    for m in attempt_sets():
        p = os.path.join(ROOT, "model_attempts", m, task_id + ".json")
        if os.path.isfile(p):
            out[m] = _load(p)
    return out


def load_task(task_id):
    return {"task": load_public_task(task_id), "truth": load_truth(task_id), "attempts": load_attempts(task_id)}


def results_sets():
    d = os.path.join(ROOT, "results")
    return sorted(x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x)))


def load_results(name):
    return {"metrics": _load(_path("results", name, "metrics.json")), "per_task": _load(_path("results", name, "per_task.json"))}


def report_md():
    p = os.path.join(ROOT, "results", "REPORT.md")
    return open(p).read() if os.path.isfile(p) else ""


def real_trace_results():
    path = os.path.join(ROOT, "rcaeval", "results_gpt-6-luna.json")
    return _load(path) if os.path.isfile(path) else None


def save_attempt(model_slug, task_id, record):
    d = _path("model_attempts", model_slug)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, task_id + ".json")
    with open(p, "w") as f:
        json.dump(record, f, indent=1)
    return p
