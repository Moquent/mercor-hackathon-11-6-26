"""Assembles the data behind summary.json and the results site. Every number is computed from the saved answers."""
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from ..config import PAGE_T, RCAEVAL_TASKS, RUNS, TASKS
from ..harness import load_attempt
from ..labels import TRAP
from ..rcaeval.score import evaluate as evaluate_rcaeval
from ..rcaeval.topology import DEPS
from ..scenario.incidents import topology_for
from ..scenario.topology import ENTRY
from ..sim import run
from ..timeline import clock
from ..verify import chain
from ..verify.claims import LOAD_SOURCES
from ..verify.extract import parse_json_block
from ..verify.replay import ops_for


def curve(incident, action_keys):
    """Entry error rate over time when the given actions are applied at page time (sampled every 5 s)."""
    rows = run(topology_for(incident), incident.faults, ENTRY, ops_for(action_keys))
    return [(r["t"], r["err"]) for r in rows if r["svc"] == ENTRY and r["t"] % 5 == 0 and r["t"] >= 60]


def node_states(incident):
    """Per-node snapshot at page time, relative to the baseline minute, for the graph."""
    rows = run(topology_for(incident), incident.faults, ENTRY)
    at = {(r["t"], r["svc"]): r for r in rows}
    out = {}
    for n in topology_for(incident):
        base, page = at[(60, n)], at[(PAGE_T, n)]
        idle = page["rps"] < 1
        out[n] = {"err": None if idle else round(page["err"], 3), "idle": idle,
                  "load_x": round(page["rps"] / max(1.0, base["rps"]), 2) if base["rps"] > 5 else None,
                  "lat_x": round(page["lat_ms"] / max(1.0, base["lat_ms"]), 2)}
    return out


def run_stability(labels):
    """Majority verdict over the runs and the share of runs that disagree with it (ties go to the first run's label)."""
    majority, _ = Counter(labels).most_common(1)[0]
    flips = sum(label != majority for label in labels) / len(labels)
    return majority, round(flips, 3)


SERIES_STEP_S = 10
EXEMPLAR_SECONDS = (PAGE_T - 60, PAGE_T)


def node_series(incident):
    """Per-node time series over the whole run, for the inspector sparklines (sampled every 10 s)."""
    rows = run(topology_for(incident), incident.faults, ENTRY)
    out = {n: {"t": [], "err": [], "lat_ms": [], "rps": []} for n in topology_for(incident)}
    for r in rows:
        if r["t"] % SERIES_STEP_S == 0:
            s = out[r["svc"]]
            s["t"].append(r["t"])
            s["err"].append(round(r["err"], 4))
            s["lat_ms"].append(round(r["lat_ms"], 1))
            s["rps"].append(round(r["rps"], 1))
    return out


def _task_record(incident, verdicts, truth, chains):
    v = verdicts[("ai", incident.id, 1)]
    att = load_attempt(incident.id, 1)
    fix = list(truth[incident.id][0]) if truth[incident.id] else []
    trap = verdicts[("trap", incident.id, 0)]
    with open(os.path.join(TASKS, incident.id, "prompt.txt")) as f:
        prompt = f.read()
    labels = [verdicts[("ai", incident.id, r)].verdict for r in RUNS if ("ai", incident.id, r) in verdicts]
    majority, flip_rate = run_stability(labels)
    return {
        "id": incident.id, "title": incident.title, "truth": incident.truth, "no_outage": incident.no_outage,
        "verdicts": [verdicts[("ai", incident.id, r)].verdict if ("ai", incident.id, r) in verdicts else None for r in RUNS],
        "majority": majority, "flip_rate": flip_rate,
        "fixed_all_seeds": v.fixed_all_seeds,
        "first": {"verdict": v.verdict, "answer": att["answer"], "model": att["model_reported"], "asked": att["asked_at_utc"],
                  "json": v.extraction.json_block, "roots": v.extraction.roots, "actions": v.extraction.actions,
                  "invariants": [c.to_dict() for c in v.invariants], "certificates": v.certificates,
                  "err_after": v.ai_error_rate_after},
        "fixes": [list(x) for x in truth[incident.id]],
        "curves": {"noop": curve(incident, []), "fix": curve(incident, fix),
                   "ai": curve(incident, list(v.extraction.actions))},
        "chain": asdict(chains[incident.id]), "nodes": node_states(incident),
        "series": node_series(incident),
        "events": [["yesterday" if e[0] < 0 else clock(e[0]), e[1], e[2], e[3]] for e in sorted(incident.events)],
        "logs": incident.logs,
        "reference": verdicts[("reference", incident.id, 0)].verdict, "trap": trap.verdict,
        "trap_cert": (trap.certificates or [""])[0],
        "trap_actions": parse_json_block(TRAP[incident.id])["actions"],
        "prompt": prompt,
    }


def build_site_data(incidents, verdicts, truth, aud):
    with ThreadPoolExecutor(8) as ex:
        chains = dict(zip([i.id for i in incidents],
                          ex.map(lambda i: chain.score(i, load_attempt(i.id, 1)["answer"]), incidents)))
    tasks = [_task_record(i, verdicts, truth, chains) for i in incidents]
    rc_rows, rc_test = evaluate_rcaeval("test")
    dev_rows, rc_dev = evaluate_rcaeval("dev")
    fresh_rows, rc_fresh = evaluate_rcaeval("fresh")
    topo = topology_for(incidents[0])
    all_ai = [v for (k, _, _), v in verdicts.items() if k == "ai"]
    ch = list(chains.values())
    return {
        "model": load_attempt(incidents[0].id, 1)["model_reported"],
        "topology": {"nodes": list(topo), "edges": [[c, p] for p, s in topo.items() for c in s.deps],
                     "jobs": [[job, svc] for job, svc in LOAD_SOURCES.items()],
                     "retries": {n: s.retries for n, s in topo.items()}},
        "tasks": tasks,
        "stats": {"tasks": len(incidents), "first_accept": sum(t["first"]["verdict"] == "ACCEPT" for t in tasks),
                  "runs": len(all_ai), "runs_accept": sum(v.verdict == "ACCEPT" for v in all_ai),
                  "mean_flip_rate": sum(t["flip_rate"] for t in tasks) / len(tasks),
                  "stable_tasks": sum(t["flip_rate"] == 0 for t in tasks),
                  "first_accept_robust": sum(t["first"]["verdict"] == "ACCEPT" and t["fixed_all_seeds"] for t in tasks),
                  "selftest_ok": sum(verdicts[("reference", t, 0)].verdict == "ACCEPT" for t in [i.id for i in incidents])
                  + sum(verdicts[("trap", t, 0)].verdict != "ACCEPT" for t in [i.id for i in incidents]),
                  "selftest_n": 2 * len(incidents),
                  "audit_ok": aud["decisions_agreeing"], "audit_n": aud["decisions_total"],
                  "chain_precision": sum(c.precision for c in ch) / len(ch),
                  "chain_recall": sum(c.recall for c in ch) / len(ch)},
        "audit": aud["rows"],
        "rcaeval": {"test": rc_test, "dev": rc_dev, "fresh": rc_fresh, "rows": rc_rows,
                    "deps": {k: list(v) for k, v in DEPS.items()}},
        "rcaeval_cases": rcaeval_cases(rc_rows + dev_rows + fresh_rows),
    }


def rcaeval_cases(rows):
    """Per-case detail for the real-data inspector: the minute series and what the AI claimed. Site only, not summary.json."""
    out = {}
    for r in rows:
        case_dir = os.path.join(RCAEVAL_TASKS, r["case"])
        with open(os.path.join(case_dir, "features.json")) as f:
            feats = json.load(f)
        entry = {"features": feats, "row": r}
        traces_path = os.path.join(case_dir, "traces.json")
        if os.path.exists(traces_path):
            with open(traces_path) as f:
                entry["traces"] = json.load(f)
        out[r["case"]] = entry
    return out
