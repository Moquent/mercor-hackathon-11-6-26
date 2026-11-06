"""The evaluation: verify every saved answer, self-test the verifier, audit extraction, and write every artefact.

Outputs:
  results/summary.json        every verdict, chain score, RCAEval metric and statistic
  results/report.html         the interactive results site (built by report/)
  docs/ground_truth.md        the computed right answer for every simulated incident
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

from .config import DOCS, JUDGE_WINDOW, PAGE_T, RESULTS, RUNS, SLO_ERROR_RATE
from .harness import load_attempt
from .audit_extra import AUDIT_EXTRA
from . import investigation
from .labels import AUDIT, REFERENCE, TRAP
from .scenario.actions import ACTIONS
from .scenario.incidents import INCIDENTS, topology_for
from .scenario.topology import ENTRY, SERVICES
from .sim import run
from .timeline import clock
from .verify.extract import parse_json_block
from .verify.replay import ground_truth, ops_for, replay, resolves_on_all_seeds
from .verify.verdict import verify
from .report.build import build
from .report.data import build_site_data


def verify_all(incidents=INCIDENTS):
    """Verdicts for every saved AI run plus the reference and trap labels. Returns (verdicts, ground truth)."""
    jobs = [("ai", i.id, r) for i in incidents for r in RUNS if load_attempt(i.id, r)]
    jobs += [("reference", i.id, 0) for i in incidents] + [("trap", i.id, 0) for i in incidents]
    by_id = {i.id: i for i in incidents}

    def go(job):
        kind, task_id, run_no = job
        if kind == "ai":
            text = load_attempt(task_id, run_no)["answer"]
        else:
            text = (REFERENCE if kind == "reference" else TRAP)[task_id]
        return job, verify(by_id[task_id], text)

    with ThreadPoolExecutor(8) as ex:
        verdicts = dict(ex.map(go, jobs))
    with ThreadPoolExecutor(8) as ex:
        truth = dict(zip([i.id for i in incidents], ex.map(ground_truth, incidents)))
    return verdicts, truth


def audit(verdicts):
    agree = total = 0
    rows = []
    for task_id, labels in AUDIT.items():
        got = set(verdicts[("ai", task_id, 1)].extraction.actions)
        for key in ACTIONS:
            total += 1
            agree += (key in got) == (key in labels)
        rows.append({"task": task_id, "hand_labels": sorted(labels), "jev": sorted(got), "exact_match": got == labels})
    return {"decisions_agreeing": agree, "decisions_total": total, "rows": rows}


def audit_repeats(verdicts):
    """The second hand audit: a seeded sample of runs 2-5, actions and named roots."""
    agree = total = roots_ok = 0
    rows = []
    for (task_id, run_no), label in sorted(AUDIT_EXTRA.items()):
        ex = verdicts[("ai", task_id, run_no)].extraction
        got = set(ex.actions)
        for key in ACTIONS:
            total += 1
            agree += (key in got) == (key in label["actions"])
        roots_ok += sorted(ex.roots) == sorted(label["roots"])
        rows.append({"task": task_id, "run": run_no, "hand_labels": sorted(label["actions"]), "jev": sorted(got),
                     "hand_roots": label["roots"], "extracted_roots": ex.roots})
    return {"decisions_agreeing": agree, "decisions_total": total, "roots_matching": roots_ok, "pairs": len(rows),
            "rows": rows}


def format_findings(verdicts):
    """Answers whose declared root_cause_service is not exactly one known service name (prose, or several services)."""
    out = []
    for (kind, task_id, run_no), v in sorted(verdicts.items()):
        named = (v.extraction.json_block or {}).get("root_cause_service")
        if kind == "ai" and named not in SERVICES:
            out.append({"task": task_id, "run": run_no, "root_cause_service": named, "read_as": v.extraction.roots})
    return out


def curve(incident, action_keys):
    rows = run(topology_for(incident), incident.faults, ENTRY, ops_for(action_keys))
    return [(r["t"], r["err"]) for r in rows if r["svc"] == ENTRY and r["t"] % 5 == 0 and r["t"] >= 60]


def node_states(incident):
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


def write_ground_truth(incidents, verdicts, truth):
    lines = ["# Ground truth", "",
             "Each simulated task is seeded, so the right answer is **computed**: every executable action in the catalog (singles, then",
             f"pairs) is replayed from the page time ({clock(PAGE_T)}). A set counts as a fix if the `{ENTRY}` mean error rate is under",
             f"{SLO_ERROR_RATE:.0%} over {clock(JUDGE_WINDOW[0])}–{clock(JUDGE_WINDOW[1])}. The rationale explains *why* in plain terms; "
             "the replay numbers are the proof.", ""]
    for inc in incidents:
        noop = replay(inc, [])[1]
        trap = verdicts[("trap", inc.id, 0)]
        trap_actions = parse_json_block(TRAP[inc.id])["actions"]
        lines += [f"## {inc.id} — {inc.title}", "", f"**What happened:** {inc.truth}", ""]
        if inc.no_outage:
            lines += [f"**Correct response:** no production change (entry error rate {noop:.1%} throughout).", ""]
        else:
            fixes = "; ".join(" + ".join(f"`{a}`" for a in s) for s in truth[inc.id])
            lines += [f"**Minimal sufficient fixes (computed):** {fixes}", "",
                      f"**Without action:** entry error rate {noop:.0%} in the judged window.", ""]
        lines += [f"**Tempting wrong answer:** {trap_actions} → verifier: "
                  f"**{trap.verdict}** (entry error rate after replay {trap.ai_error_rate_after:.0%}).", ""]
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "ground_truth.md"), "w") as f:
        f.write("\n".join(lines))


def run_evaluation():
    os.makedirs(RESULTS, exist_ok=True)
    verdicts, truth = verify_all()
    aud = audit(verdicts)
    aud2 = audit_repeats(verdicts)
    fmt = format_findings(verdicts)
    inv_runs, inv_truth, inv_self = investigation.evaluate()
    inv_stats = investigation.stats(inv_runs, {i.id: verdicts[("ai", i.id, 1)].verdict for i in INCIDENTS})
    write_ground_truth(INCIDENTS, verdicts, truth)
    data = build_site_data(INCIDENTS, verdicts, truth, aud)
    data["investigation"] = {"stats": inv_stats, "selftest": inv_self, "runs": inv_runs,
                             "audit_repeats": aud2, "format_findings": fmt}
    build(data, os.path.join(RESULTS, "report.html"))
    robust = {inc.id: [resolves_on_all_seeds(inc, list(s)) for s in truth[inc.id]] for inc in INCIDENTS}
    summary = {"verdicts": [{"kind": k, "task": t, "run": r, **v.to_dict()} for (k, t, r), v in verdicts.items()],
               "ground_truth": {t: [list(s) for s in v] for t, v in truth.items()},
               "ground_truth_robust": robust,
               "extraction_audit": aud,
               "causal_chains": {t["id"]: t["chain"] for t in data["tasks"]},
               "rcaeval": data["rcaeval"], "stats": data["stats"],
               "extraction_audit_repeats": aud2, "format_findings": fmt,
               "investigation": {"stats": inv_stats, "selftest": inv_self, "ground_truth": inv_truth,
                                 "runs": [{k: r[k] for k in ("task", "run", "verdict", "trace")} for r in inv_runs]}}
    with open(os.path.join(RESULTS, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=list)
    for inc in INCIDENTS:
        runs = " ".join(verdicts[("ai", inc.id, r)].verdict if ("ai", inc.id, r) in verdicts else "-" for r in RUNS)
        print(f"{inc.id:22s} {runs} | ref {verdicts[('reference', inc.id, 0)].verdict} "
              f"trap {verdicts[('trap', inc.id, 0)].verdict}")
    print(json.dumps(data["stats"], indent=1))
    print("rcaeval test:", json.dumps(data["rcaeval"]["test"]))
    print("investigation:", json.dumps(inv_stats))
    print("trace self-test:", sum(c["pass"] for c in inv_self), "of", len(inv_self))
    print("audit repeats:", aud2["decisions_agreeing"], "of", aud2["decisions_total"], "roots", aud2["roots_matching"], "of", aud2["pairs"])
    return 0
