"""The console's jobs. Each one does the real work (the same functions the batch evaluation uses) and emits every
intermediate result as an event, in the order defined in EVENTS.md.
"""
import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, fields

from ..agent.run import AGENT_INCIDENTS, attempt_path, investigate, load
from ..clients import ask_subject
from ..config import AGENT_ATTEMPTS, JUDGE_WINDOW, PAGE_T, SEEDS, SIM_T
from ..scenario.actions import ALL_ACTIONS, NET_EXTRA_ACTIONS
from ..scenario.actions import catalog_for as _catalog_for
from ..scenario.incidents import topology_for
from ..scenario.topology import ENTRY, SERVICES
from ..sim import Fault, error_rate, run
from ..verify.replay import ground_truth, replay
from ..verify.trace import verify_trace
from ..verify.verdict import verify
from . import cases

def catalog_for(task_id):
    """Drafted cases (ids start with x-) may use any link mode, so they get every action."""
    return ALL_ACTIONS | NET_EXTRA_ACTIONS if task_id.startswith("x-") else _catalog_for(task_id)


STEP_DELAY_S = 0.6
CLAIM_DELAY_S = 0.15
_truth_cache = {}


def truth_for(incident):
    if incident.id not in _truth_cache:
        _truth_cache[incident.id] = ground_truth(incident, catalog_for(incident.id))
    return _truth_cache[incident.id]


def _curve(rows):
    return [[r["t"], round(r["err"], 4)] for r in rows if r["svc"] == ENTRY and r["t"] % 5 == 0 and r["t"] >= 60]


def sim_payload(incident):
    """Per-second state of every service, for the animated topology."""
    topo = topology_for(incident)
    rows = run(topo, incident.faults, ENTRY)
    services = {n: {"rps": [], "lat_ms": [], "err": []} for n in topo}
    for r in rows:
        s = services[r["svc"]]
        s["rps"].append(round(r["rps"], 1))
        s["lat_ms"].append(round(r["lat_ms"], 1))
        s["err"].append(round(r["err"], 3))
    links = [{"src": f.svc, "dst": f.link, "mode": getattr(f, "mode", "refuse"), "value": f.value, "start": f.start,
              "end": min(f.end, SIM_T)} for f in incident.faults if f.link]
    return {"incident_id": incident.id, "title": incident.title, "t": list(range(SIM_T)), "page_t": PAGE_T,
            "services": services, "links": links, "faults": [asdict(f) | {"end": min(f.end, SIM_T)} for f in incident.faults],
            "truth": incident.truth, "events": [list(e) for e in incident.events], "logs": incident.logs,
            "ground_truth": [list(s) for s in truth_for(incident)]}


# ---------- verification, streamed ----------

def verify_streamed(job, incident, record):
    answer = record.get("answer") or ""
    job.emit("stage", name="extract", status="start")
    v = verify(incident, answer, catalog_for(incident.id))            # Jev answers come from the cache when the answer was seen before
    ex = v.extraction
    job.emit("extract", actions=ex.action_probs, claimed=list(ex.actions), roots=ex.roots, blamed=ex.blamed)
    job.emit("stage", name="extract", status="done")

    job.emit("stage", name="replay", status="start")
    job.emit("replay_baseline", curve=_curve(replay(incident, [])[0]))
    truth = truth_for(incident)
    if truth:
        job.emit("replay_baseline", truth_curve=_curve(replay(incident, list(truth[0]))[0]), truth_actions=list(truth[0]))
    for seed in SEEDS:
        rows, err = replay(incident, list(ex.actions), seed)
        job.emit("replay_seed", seed=seed, fixed=err < 0.05, err_after=round(err, 4), curve=_curve(rows))
        time.sleep(CLAIM_DELAY_S)
    job.emit("stage", name="replay", status="done")

    job.emit("stage", name="story", status="start")
    for c in v.invariants:
        job.emit("invariant", check=c.name, detail=c.detail, **{"pass": c.passed})
        time.sleep(CLAIM_DELAY_S)
    job.emit("stage", name="story", status="done")

    tr = verify_trace(incident, record)
    job.emit("stage", name="claims", status="start")
    for i, c in enumerate(tr["claims"]):
        job.emit("claim", index=i, text=c["text"], claim=c["claim"], grounded=c["grounded"], supported=c["supported"],
                 label=c["label"], evidence=c["evidence"], decided_by=c.get("decided_by"), commit_step=c["commit_step"], **{"true": c["true"]})
        time.sleep(CLAIM_DELAY_S)
    job.emit("stage", name="claims", status="done")

    job.emit("stage", name="missed", status="start")
    for m in tr["missed_checks"]:
        job.emit("missed_check", **m)
        time.sleep(CLAIM_DELAY_S)
    if tr["first_wrong"]:
        job.emit("first_wrong", **tr["first_wrong"])
    job.emit("stage", name="missed", status="done")

    job.emit("stage", name="verdict", status="start")
    job.emit("verdict", verdict=v.verdict, fixed_in_replay=v.fixed_in_replay, fixed_by_seed=v.fixed_by_seed,
             ai_error_rate_after=v.ai_error_rate_after, clean_trace=tr["clean"], counts=tr["counts"],
             certificates=v.certificates)
    job.emit("stage", name="verdict", status="done")


# ---------- jobs ----------

def _need(task_id):
    inc = cases.find(task_id)
    if inc is None:
        raise ValueError(f"unknown incident {task_id}")
    return inc


def job_replay(job, task_id, run_no, speed=1.0):
    inc = _need(task_id)
    rec = load(task_id, run_no)
    if rec is None:
        raise ValueError(f"no saved run {task_id} run {run_no}")
    job.emit("job_started", kind="replay", task=task_id, run=run_no)
    job.emit("log", level="info", msg=f"replaying saved investigation {task_id} run {run_no} ({len(rec['steps'])} steps, "
                                      f"model {rec.get('model_reported')})")
    delay = STEP_DELAY_S / max(0.1, speed)
    by_exchange = {}
    for s in rec["steps"]:
        by_exchange.setdefault(s["exchange"], []).append(s)
    for i, x in enumerate(rec["exchanges"]):
        job.emit("request_sent", exchange=i, n_input_items=1 if isinstance(x["request"]["input"], str) else len(x["request"]["input"]))
        job.emit("response", exchange=i, usage=x["response"].get("usage"), latency_ms=None)
        for s in by_exchange.get(i, []):
            for text in s.get("reasoning_summary") or []:
                job.emit("reasoning", exchange=i, text=text)
            a = s["args"] if isinstance(s["args"], dict) else {}
            focus = {"nodes": [a["src"], a["dst"]], "edge": [a["src"], a["dst"]]} if a.get("src") and a.get("dst") else \
                {"nodes": [a["service"]] if a.get("service") else [], "edge": None}
            job.emit("tool_call", step=s["id"], tool=s["tool"], args=a, reason=s.get("reason"), focus=focus)
            time.sleep(delay / 2)
            job.emit("tool_result", step=s["id"], tool=s["tool"], output=s["output"], observed=s["observed"])
            time.sleep(delay / 2)
    for text in rec.get("final_reasoning") or []:
        job.emit("reasoning", exchange=len(rec["exchanges"]) - 1, text=text)
    from ..verify.extract import parse_json_block
    job.emit("answer", text=rec.get("answer"), json=parse_json_block(rec.get("answer") or ""))
    verify_streamed(job, inc, rec)


def job_investigate(job, task_id):
    inc = _need(task_id)
    job.emit("job_started", kind="investigate", task=task_id, run=None)
    job.emit("log", level="info", msg=f"live investigation of {task_id}: real requests to the subject model")
    path = os.path.join(AGENT_ATTEMPTS, "live", f"{task_id}.{job.id}.json")
    investigate(inc, 0, emit=job.emit, path=path)
    with open(path) as f:
        rec = json.load(f)
    job.emit("log", level="info", msg=f"saved full record to {os.path.relpath(path)}")
    verify_streamed(job, inc, rec)


def job_verify(job, task_id, run_no):
    inc = _need(task_id)
    rec = load(task_id, run_no)
    job.emit("job_started", kind="verify", task=task_id, run=run_no)
    verify_streamed(job, inc, rec)


def job_bench(job):
    jobs = [(inc, r) for inc in AGENT_INCIDENTS for r in (1, 2, 3) if os.path.exists(attempt_path(inc.id, r))]
    job.emit("job_started", kind="bench", task=None, run=None)
    job.emit("log", level="info", msg=f"re-verifying {len(jobs)} saved investigations (replay on {len(SEEDS)} seeds + trace)")
    tot = {"done": 0, "total": len(jobs), "accept": 0, "claims": 0, "verified": 0, "false": 0, "clean": 0}

    def one(j):
        inc, r = j
        rec = load(inc.id, r)
        v = verify(inc, rec.get("answer") or "", catalog_for(inc.id))
        return inc.id, r, v, verify_trace(inc, rec)

    with ThreadPoolExecutor(6) as ex:
        for fut in as_completed([ex.submit(one, j) for j in jobs]):
            task, r, v, tr = fut.result()
            c = tr["counts"]
            tot["done"] += 1
            tot["accept"] += v.verdict == "ACCEPT"
            tot["claims"] += sum(c.values())
            tot["verified"] += c["VERIFIED"]
            tot["false"] += c["FALSE"]
            tot["clean"] += tr["clean"]
            job.emit("bench_row", task=task, run=r, verdict=v.verdict, counts=c, clean=tr["clean"],
                     fixed_by_seed=v.fixed_by_seed, first_wrong=tr["first_wrong"])
            job.emit("bench_totals", **tot)


# ---------- the case composer ----------

DRAFT_PROMPT = """You design realistic production incidents for a simulator of "Larkwave", a music streaming backend.
Turn the description below into ONE incident as JSON. Use only these services and dependencies:
{deps}

Fault fields: svc (a service), field ("err" own error share 0-1 | "capacity" req/s | "extra_rps" extra background load | "link"),
value, start (seconds, 60-200; the on-call is paged at 300), end (optional), kind ("transient": cleared by restart |
"config": cleared by rolling back `change` | "external": cleared by restoring the provider), change (one of: {changes}),
and for field "link": link (a direct dependency of svc) and mode ("refuse" | "dns" | "tls" | "latency" | "loss").
value must be a number: for refuse/dns/tls/loss, the fraction of calls that fail, from 0 to 1; for latency, added milliseconds;
for err, the error fraction from 0 to 1; for capacity or extra_rps, requests per second. Never put an IP address in value;
an address belongs in the logs or event description. Capacity must be greater than zero.
Write events (deploys, config changes, alerts, including one plausible red herring) and the error log line each affected service
would print. Keep it subtle: logs show symptoms, not the answer.

Description: {description}

Answer with only a JSON block:
```json
{{"id": "short-kebab-id", "title": "...", "faults": [{{"id": "F1", "svc": "...", "field": "...", "value": 0.0, "start": 120, "kind": "config", "change": "...", "link": "", "mode": "refuse"}}],
  "events": [[110, "<service>", "deploy|config|alert|infra|vendor", "message"]], "logs": {{"<service>": "sample error line"}},
  "truth": "what really happened and what fixes it"}}
```"""


def _changes():
    return sorted({op[1] for a in (ALL_ACTIONS | NET_EXTRA_ACTIONS).values() for op in a.ops if op[0] == "rollback"})


def _validate(d):
    topo = topology_for(cases.find("01-vela-402"))
    allowed = {f.name for f in fields(Fault)}
    problems = []
    faults = []
    if not isinstance(d, dict):
        return {"faults": []}, ["the draft must be a JSON object"]
    if not isinstance(d.get("faults"), list):
        return {"faults": []}, ["faults must be a list"]
    for i, f in enumerate(d.get("faults") or []):
        if not isinstance(f, dict):
            problems.append(f"fault {i + 1} must be an object")
            continue
        f = {k: v for k, v in f.items() if k in allowed}
        f.setdefault("id", f"F{i + 1}")
        if f.get("svc") not in topo:
            problems.append(f"unknown service {f.get('svc')}")
            continue
        if f.get("field") == "link" or f.get("link"):
            if f.get("link") not in topo[f["svc"]].deps:
                problems.append(f"{f['svc']} has no dependency {f.get('link')}")
                continue
            f["field"] = "link"
            if f.get("mode", "refuse") not in ("refuse", "dns", "tls", "latency", "loss"):
                problems.append(f"unsupported link mode {f.get('mode')}")
                continue
        elif f.get("field") not in ("err", "capacity", "extra_rps"):
            problems.append(f"unsupported field {f.get('field')}")
            continue
        if not f.get("link"):
            f.pop("mode", None)
            f.pop("link", None)
        try:
            if isinstance(f.get("value"), bool):
                raise ValueError()
            value = float(f["value"])
            if not math.isfinite(value) or value < 0 or (f["field"] == "capacity" and value == 0):
                raise ValueError()
            if (f["field"] == "err" or f.get("link") and f.get("mode", "refuse") != "latency") and value > 1:
                raise ValueError()
            f["value"] = value
            f["start"] = int(f.get("start", 120))
            if not 30 <= f["start"] <= 250:
                raise ValueError()
            if f.get("end") is None:
                f.pop("end", None)
            else:
                f["end"] = int(f["end"])
                if f["end"] <= f["start"]:
                    raise ValueError()
        except (ValueError, TypeError, KeyError, OverflowError):
            problems.append(f"fault {i + 1} needs a valid numeric value and a start/end in range")
            continue
        if f.get("kind", "transient") not in ("transient", "config", "external"):
            problems.append(f"unsupported fault kind {f.get('kind')}")
            continue
        if f.get("kind") == "config" and f.get("change") not in _changes():
            problems.append(f"fault {i + 1} needs a rollback change from the action catalogue")
            continue
        faults.append(f)
    if not faults:
        problems.append("no usable fault")
    d["faults"] = faults
    try:
        d["events"] = [[int(e[0]), str(e[1]), str(e[2]), str(e[3])] for e in d.get("events", []) if len(e) == 4]
        d["logs"] = {k: str(v) for k, v in (d.get("logs") or {}).items() if k in topo}
    except (ValueError, TypeError, AttributeError):
        problems.append("events or logs have an invalid format")
        d["events"], d["logs"] = [], {}
    d["id"] = "x-" + re.sub(r"[^a-z0-9-]", "-", str(d.get("id", "custom")).lower())[:40]
    d["title"] = "CUSTOM: " + str(d.get("title", d["id"]))
    return d, problems


def job_draft(job, description):
    from ..scenario.topology import DEPS_TEXT
    from ..verify.extract import parse_json_block
    job.emit("job_started", kind="draft", task=None, run=None)
    prompt = DRAFT_PROMPT.format(deps=DEPS_TEXT, changes=", ".join(_changes()), description=description)
    job.emit("log", level="info", msg="asking the subject model to draft the case")
    for attempt in range(2):
        text, raw = ask_subject(prompt)
        job.emit("draft_text", text=text)
        block = parse_json_block(text)
        if block is None and "{" in text:
            try:
                block = json.loads(text[text.index("{"):text.rindex("}") + 1])
            except (json.JSONDecodeError, ValueError):
                block = None
        case, problems = _validate(block)
        if not problems:
            break
        for p in problems:
            job.emit("log", level="warn", msg=p)
        if attempt == 0:
            job.emit("log", level="info", msg="repairing the invalid draft")
            prompt += "\n\nYour previous draft was invalid: " + "; ".join(problems) + "\nCorrect it and return the complete JSON again.\nPrevious draft:\n" + text
    else:
        job.emit("case_error", error="; ".join(problems))
        return
    inc = cases.save(case)
    rows = run(topology_for(inc), inc.faults, ENTRY)
    before = error_rate(rows, ENTRY, *JUDGE_WINDOW)
    job.emit("log", level="info", msg="computing ground truth: replaying every catalogue action, then pairs")
    truth = truth_for(inc)
    job.emit("case_ready", case=case, ground_truth=[list(s) for s in truth], entry_err_before=round(before, 4),
             services_known=SERVICES)
