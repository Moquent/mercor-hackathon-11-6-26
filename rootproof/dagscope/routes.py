"""HTTP routes for the DAGScope page. The server calls these and sends (code, obj) as JSON.

GET  /api/dag/runs                 -> [{scenario_id, run_id, n_events, n_dependencies, services, coverage}]
GET  /api/dag/run/<scenario_id>    -> {events, dependencies, coverage, manifest, names, integrity, structural_issues}
GET  /api/dag/tasks                -> [{task_id, scenario_id, question, coverage, difficulty, n_pairs, attempts}]
GET  /api/dag/task/<task_id>       -> {task, truth, attempts: {set: {record, graded}}, hidden_edges, hidden_event_ids, prompt}
GET  /api/dag/results              -> {report_md, sets: {name: metrics}}
POST /api/dag/verify               {run, claims:[...], coverage?, task?} -> {verdicts:[...], coverage, structural_issues}
POST /api/dag/ask                  {task_id} -> {job}: streams dag_* events (see below)

Events of a dag-ask job: job_started, log, request_sent {task_id, model, prompt_chars}, answer {text, json},
dag_claim {index, raw, slot_index, verdict, explanation, dependency_path, evidence, reachable_in_private_graph},
dag_grade {task_correct, format_failure, mismatch, slot_results, verdict_counts, saved}, error, done.
"""
import time
from datetime import datetime, timezone

from . import data
from . import verify as V

ASK_MODEL_SLUG = "gpt-6-luna"


def _names(artifact):
    return {v: k for k, v in (artifact.get("manifest") or {}).items()}


def _run_payload(sid):
    a = data.load_artifact(sid)
    g = V.build_dependency_dag(a["events"], a["dependencies"], a["coverage"])
    run = data.load_run(sid)
    run["names"] = _names(a)
    run["structural_issues"] = g.meta["structural_issues"]
    run["integrity_check"] = V.validate_graph_integrity(g, a["events"])
    return run


def _task_payload(tid):
    t = data.load_task(tid)
    task, truth = t["task"], t["truth"]
    art = data.load_artifact(task["scenario_id"])
    mask = truth["ground_truth"]["mask"]
    view = V.task_view(art, truth)
    attempts = {}
    for name, rec in t["attempts"].items():
        attempts[name] = {"record": {k: rec.get(k) for k in ("source", "model_name", "condition", "evaluated_at_utc",
                                                             "raw_response", "parsed", "parse_error", "fixture_label")},
                          "graded": V.grade(rec, task, truth, art, graph_cov=view)}
    return {"task": task, "truth": truth, "attempts": attempts, "names": _names(art),
            "hidden_edges": V.hidden_edges(art, mask), "hidden_event_ids": truth["ground_truth"].get("hidden_event_ids", []),
            "prompt": V.build_prompt(task, "explicit_parents")}


def _model_summary():
    rows = []
    for item in data.list_tasks():
        if ASK_MODEL_SLUG not in item["attempts"]:
            continue
        t = data.load_task(item["task_id"])
        art = data.load_artifact(t["task"]["scenario_id"])
        grade = V.grade(t["attempts"][ASK_MODEL_SLUG], t["task"], t["truth"], art)
        rows.append(grade)
    bins = []
    for low, high in ((0.0, 0.5), (0.5, 0.75), (0.75, 1.01)):
        group = [r for r in rows if isinstance(r.get("confidence"), (int, float)) and low <= r["confidence"] < high]
        bins.append({"range": f"{low:.2f}–{min(high, 1.0):.2f}", "tasks": len(group),
                     "correct": sum(r["task_correct"] for r in group)})
    return {"model": ASK_MODEL_SLUG, "tasks": len(rows), "correct": sum(r["task_correct"] for r in rows),
            "format_failures": sum(r["format_failure"] for r in rows), "confidence_bins": bins}


def handle_get(path):
    try:
        if path == "/api/dag/runs":
            return 200, data.list_runs()
        if path.startswith("/api/dag/run/"):
            return 200, _run_payload(path.rsplit("/", 1)[1])
        if path == "/api/dag/tasks":
            return 200, data.list_tasks()
        if path.startswith("/api/dag/task/"):
            return 200, _task_payload(path.rsplit("/", 1)[1])
        if path == "/api/dag/results":
            return 200, {"report_md": data.report_md(),
                         "sets": {n: data.load_results(n)["metrics"] for n in data.results_sets()},
                         "real_traces": data.real_trace_results(), "event_tasks": _model_summary()}
        if path == "/api/dag/health":
            from .runner import healthy
            return 200, {"real_system_available": healthy()}
    except (FileNotFoundError, ValueError) as e:
        return 404, {"error": f"not found: {e}"}
    return None


def verify_request(body):
    run = body.get("run")
    if not isinstance(run, str):
        return 400, {"error": "run (scenario id) required"}
    claims = body.get("claims")
    if not isinstance(claims, list):
        return 400, {"error": "claims must be a list"}
    try:
        art = data.load_artifact(run)
        if body.get("task"):
            g, cov = V.task_view(art, data.load_truth(body["task"]))
        else:
            cov = body.get("coverage") if isinstance(body.get("coverage"), dict) else art["coverage"]
            g = V.build_dependency_dag(art["events"], art["dependencies"], cov)
    except (FileNotFoundError, ValueError) as e:
        return 404, {"error": f"not found: {e}"}
    resolved, warnings = V.resolve_coverage(g, cov)
    out = []
    for c in claims[:200]:
        r = V.verify_claim(c, g, cov)
        if r["dependency_path"]:
            r["path_text"] = " -> ".join(f"{g.nodes[n].get('event_type', '?')}({n[:8]})" for n in r["dependency_path"])
        out.append(r)
    return 200, {"verdicts": out, "coverage": resolved, "structural_issues": warnings}


def ask_job(job, task_id):
    """Ask the subject model one task (fresh conversation), verify every claim, grade, save. Never overwrites a first attempt."""
    from .. import clients
    from ..config import SUBJECT_MODEL
    t = data.load_task(task_id)
    task, truth = t["task"], t["truth"]
    art = data.load_artifact(task["scenario_id"])
    job.emit("job_started", kind="dag-ask", task=task_id, run=None)
    prompt = V.build_prompt(task, "explicit_parents")
    job.emit("request_sent", exchange=1, n_input_items=1, task_id=task_id, model=SUBJECT_MODEL, prompt_chars=len(prompt))
    t0 = time.time()
    text, raw = clients.ask_subject(prompt)
    latency = int((time.time() - t0) * 1000)
    parsed, error = V.parse_answer(text)
    job.emit("response", exchange=1, usage=(raw or {}).get("usage") or {}, latency_ms=latency)
    job.emit("answer", text=text, json=parsed)
    if error:
        job.emit("log", level="warn", msg=f"unparseable answer: {error}")
    record = {"source": "model", "task_id": task_id, "condition": "explicit_parents", "model_name": SUBJECT_MODEL,
              "response_model": (raw or {}).get("model"), "endpoint_host": "api.openai.com",
              "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
              "conversation": "fresh conversation; single user message; no system prompt or history",
              "prompt": prompt, "raw_response": text, "parsed": parsed, "parse_error": error,
              "token_usage": (raw or {}).get("usage"), "latency_ms": latency}
    view = V.task_view(art, truth)
    graded = V.grade(record, task, truth, art, graph_cov=view)
    for i, c in enumerate(graded["claims"]):
        r = c["result"]
        job.emit("dag_claim", index=i, raw=c["raw"], slot_index=c.get("slot_index"), verdict=r["verdict"],
                 explanation=r["explanation"], dependency_path=r["dependency_path"], evidence=r["evidence"],
                 reachable_in_private_graph=c["reachable_in_private_graph"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    existing = data.load_attempts(task_id).get(ASK_MODEL_SLUG)
    # First attempt is kept as THE attempt (DAGScope protocol); later ones are saved alongside with a timestamp.
    name = task_id if existing is None else f"{task_id}.{stamp}"
    path = data.save_attempt(ASK_MODEL_SLUG, name, record) if name == task_id else _save_extra(name, record)
    job.emit("dag_grade", task_correct=graded["task_correct"], format_failure=graded["format_failure"],
             mismatch=graded["mismatch"], slot_results=graded["slot_results"], verdict_counts=V.verdict_counts([graded]),
             saved=path)


def _save_extra(name, record):
    import json
    import os
    d = os.path.join(data.ROOT, "model_attempts", ASK_MODEL_SLUG, "later")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + ".json")
    with open(p, "w") as f:
        json.dump(record, f, indent=1)
    return p


def handle_post(path, body, bus):
    if path == "/api/dag/verify":
        return verify_request(body if isinstance(body, dict) else {})
    if path == "/api/dag/ask":
        tid = (body or {}).get("task_id")
        if not isinstance(tid, str):
            return 400, {"error": "task_id required"}
        try:
            data.load_public_task(tid)
        except (FileNotFoundError, ValueError):
            return 404, {"error": "unknown task"}
        job = bus.start("dag-ask", ask_job, tid)
        return 200, {"job": job.id}
    if path == "/api/dag/run-live":
        from . import runner
        sid = (body or {}).get("scenario")
        if sid not in runner.SCENARIOS:
            return 400, {"error": "unknown scenario"}
        def run_job(job, scenario):
            job.emit("job_started", kind="dag-run", task=scenario)
            if not runner.healthy():
                job.emit("log", level="warn", msg=runner.NOT_RUNNING + "; displaying the captured run")
                job.emit("dag_captured", scenario=scenario)
                return
            runner.run_scenario(scenario, lambda kind, payload: job.emit(kind, **payload))
        job = bus.start("dag-run", run_job, sid)
        return 200, {"job": job.id}
    return None
