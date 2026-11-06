"""External benchmark tasks built from real RCAEval traces, kept apart from the primary 12 tasks.

Task families:
  reachability  Real failing span, plus a sibling and a nearby failing span from another trace.
  gap           A real orphan span whose parent the capture never recorded.
  attribution   Which service had the fault injected? Graded against the experiment's label.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from app.evaluation.prompts import DEFINITIONS
from app.external.rcaeval import OPEN_WORLD, failed_mask, latency_table, load_case, naive_error_blame, render_spans, spans_to_records
from app.scenarios.ground_truth import ancestors_of, bfs_path, closure
from app.scenarios.tasks import ANCESTOR, REACH

ROOT = Path(__file__).resolve().parents[2] / "benchmark" / "rcaeval"
MAX_TRACE_SPANS = 60
SYSTEM_NAMES = {"ob": "Online Boutique (Google's microservice demo: gRPC services behind a frontend)",
                "tt": "Train Ticket (a large Java microservice ticketing system)"}
_REL_DEFS = "\n".join(line for line in DEFINITIONS.splitlines() if "commit" not in line)


class FaultAnswer(BaseModel):
    injected_fault_service: str
    explanation: str
    confidence: float = Field(ge=0.0, le=1.0)


def _system(case: str) -> str:
    return case.split("_")[0][-2:]


def _trace_sizes(spans: pd.DataFrame) -> pd.Series:
    return spans.groupby("traceID").size()


def _slot(index: int, source: str, target: str, source_name: str, target_name: str, adj: dict, allowed: list[str]) -> dict[str, Any]:
    path = bfs_path(adj, source, target)
    truth = {"slot_index": index, "source": source, "target": target, "source_name": source_name, "target_name": target_name, "allowed": allowed,
             "path": path or [], "reachable_in_complete_private_graph": path is not None}
    truth["relationship"] = "reachable" if path else "insufficient_evidence"
    return truth


def _ancestor_slot(index: int, source: str, target: str, source_name: str, target_name: str, deps: list[dict]) -> dict[str, Any]:
    shared = ancestors_of(deps, source) & ancestors_of(deps, target)
    return {"slot_index": index, "source": source, "target": target, "source_name": source_name, "target_name": target_name, "allowed": ANCESTOR, "path": [],
            "reachable_in_complete_private_graph": False, "relationship": "shared_ancestor" if shared else "insufficient_evidence", "acceptable_ancestors": sorted(shared)}


def _prompt_pairs(slots: list[dict]) -> list[dict[str, Any]]:
    return [{"source_event_id": s["source"], "target_event_id": s["target"], "allowed_relationships": s["allowed"], "ancestor_required": s["allowed"] == ANCESTOR} for s in slots]


def _assemble(task_id: str, family: str, case: str, question: str, spans: pd.DataFrame, traces: list[str], slots: list[dict], names: dict[str, str]) -> tuple[dict, dict, dict]:
    subset = spans[spans.traceID.isin(traces)]
    events, deps = spans_to_records(subset, case)
    # An orphan's parent stays referenced but has no line of its own: that gap is real, not masked.
    logs = render_spans(events)
    public = {"task_id": task_id, "scenario_id": "rcaeval_reachability", "family": family, "system": _system(case), "question": question, "pairs": _prompt_pairs(slots),
              "coverage": "partial", "difficulty": "hard", "ai_visible_logs": logs}
    private = {"task_id": task_id, "scenario_id": "rcaeval_reachability", "family": family, "case": case, "question": question, "ai_visible_logs": logs, "coverage": "partial", "difficulty": "hard",
               "expected_answer": "; ".join(f"{s['source_name']} -> {s['target_name']}: {s['relationship']}" for s in slots),
               "ground_truth": {"slots": slots, "mask": None, "hidden_event_ids": [], "computed_by": "hand-written BFS over the span parent links"},
               "evidence_event_ids": sorted({n for s in slots for n in s["path"]} | {s["source"] for s in slots} | {s["target"] for s in slots})}
    artifact = {"case": case, "events": events, "dependencies": deps, "coverage": OPEN_WORLD.model_dump(), "manifest": {v: k for k, v in names.items()},
                "sql_closure": sorted(list(p) for p in closure(deps))}
    return public, private, artifact


def reachability_task(case: str, spans: pd.DataFrame, inject_ms: int) -> tuple[dict, dict, dict] | None:
    """First post-injection failing span in a small, gap-free trace, with a sibling and a nearby failure elsewhere."""
    sizes = _trace_sizes(spans)
    failed = failed_mask(spans)
    after = spans[(spans.startTimeMillis >= inject_ms) & failed]
    small = after[after.traceID.map(sizes) <= MAX_TRACE_SPANS]
    for _, error in small.iterrows():
        trace = spans[spans.traceID == error.traceID]
        ids = set(trace.spanID)
        parents = trace.parentSpanID.dropna()
        if (~parents.isin(ids) & (parents != "")).any():
            continue
        siblings = trace[(trace.parentSpanID == error.parentSpanID) & (trace.spanID != error.spanID)]
        roots = trace[trace.parentSpanID.isna() | (trace.parentSpanID == "")]
        if siblings.empty or roots.empty:
            continue
        others = small[small.traceID != error.traceID]
        if others.empty:
            continue
        nearest = others.iloc[(others.startTime - error.startTime).abs().argsort().iloc[0]]
        traces = [error.traceID, nearest.traceID]
        subset = spans[spans.traceID.isin(traces)]
        _, deps = spans_to_records(subset, case)
        adj: dict[str, list[str]] = {}
        for d in deps:
            adj.setdefault(d["parent_event_id"], []).append(d["child_event_id"])
        root, sibling = roots.spanID.iloc[0], siblings.spanID.iloc[0]
        names = {root: "trace_root", error.spanID: "failing_span", sibling: "sibling_span", nearest.spanID: "nearby_failure_other_trace"}
        slots = [
            _slot(0, root, error.spanID, "trace_root", "failing_span", adj, REACH),
            _slot(1, nearest.spanID, error.spanID, "nearby_failure_other_trace", "failing_span", adj, REACH),
            _slot(2, sibling, error.spanID, "sibling_span", "failing_span", adj, REACH),
            _ancestor_slot(3, sibling, error.spanID, "sibling_span", "failing_span", deps),
        ]
        question = "Two failing requests overlapped during an incident. Using only the recorded trace dependencies, evaluate each listed span pair, including whether the failure close in time is connected."
        return _assemble(f"rcaeval_reach_{case}", "reachability", case, question, spans, traces, slots, names)
    return None


def gap_task(case: str, spans: pd.DataFrame) -> tuple[dict, dict, dict] | None:
    """Smallest trace containing a real orphan and a root with at least one recorded child."""
    known = set(spans.spanID)
    parents = spans.parentSpanID
    orphans = spans[parents.notna() & (parents != "") & ~parents.isin(known)]
    if orphans.empty:
        return None
    sizes = _trace_sizes(spans)
    candidates = sorted(set(orphans.traceID), key=lambda t: (sizes[t], t))
    for trace_id in candidates:
        if sizes[trace_id] > MAX_TRACE_SPANS:
            break
        trace = spans[spans.traceID == trace_id]
        roots = trace[trace.parentSpanID.isna() | (trace.parentSpanID == "")]
        if roots.empty:
            continue
        root = roots.spanID.iloc[0]
        children = trace[trace.parentSpanID == root]
        trace_orphans = orphans[orphans.traceID == trace_id]
        if children.empty:
            continue
        orphan = trace_orphans.spanID.iloc[0]
        _, deps = spans_to_records(trace, case)
        adj: dict[str, list[str]] = {}
        for d in deps:
            adj.setdefault(d["parent_event_id"], []).append(d["child_event_id"])
        child = children.spanID.iloc[0]
        names = {root: "trace_root", orphan: "orphan_span", child: "recorded_child"}
        slots = [_slot(0, root, orphan, "trace_root", "orphan_span", adj, REACH), _slot(1, root, child, "trace_root", "recorded_child", adj, REACH)]
        question = "Both spans listed below belong to the same trace id. Using only the recorded trace dependencies, can this export establish that the trace's root reached each one?"
        return _assemble(f"rcaeval_gap_{case}", "gap", case, question, spans, [trace_id], slots, names)
    return None


def attribution_task(case: str, spans: pd.DataFrame, inject_ms: int, label: dict[str, str], candidates: list[str]) -> tuple[dict, dict]:
    sizes = _trace_sizes(spans)
    after = spans[(spans.startTimeMillis >= inject_ms) & (spans.traceID.map(sizes) <= MAX_TRACE_SPANS)]
    failing = after[failed_mask(after)]
    pool = failing if len(failing) else after
    exemplar = pool.sort_values(["duration", "spanID"], ascending=[False, True]).traceID.iloc[0]
    events, _ = spans_to_records(spans[spans.traceID == exemplar], case)
    public = {"task_id": f"rcaeval_attr_{case}", "scenario_id": "rcaeval_attribution", "family": "attribution", "system": _system(case), "coverage": "partial", "difficulty": "hard",
              "candidates": candidates, "incident_start_utc": pd.Timestamp(inject_ms, unit="ms", tz="UTC").isoformat(),
              "question": "A fault was deliberately injected into exactly one service at the incident start time. Which service was it?",
              "telemetry_table": latency_table(spans, inject_ms), "exemplar_trace": render_spans(events)}
    private = {"task_id": public["task_id"], "scenario_id": "rcaeval_attribution", "family": "attribution", "case": case, "coverage": "partial", "difficulty": "hard",
               "expected_answer": label["root_cause_service"],
               "ground_truth": {"injected_service": label["root_cause_service"], "fault": label["fault"], "fault_description": label["fault_description"], "source": "RCAEval experiment label (fault injection)",
                                "service_present_in_traces": label["root_cause_service"] in set(spans.serviceName), "naive_error_blame_baseline": naive_error_blame(spans, inject_ms)},
               "evidence_event_ids": []}
    return public, private


def build_prompt(task: dict[str, Any]) -> str:
    if task["family"] == "attribution":
        return f"""You are helping investigate an incident in {SYSTEM_NAMES[task['system']]}.

{task['question']}
Incident start (UTC): {task['incident_start_utc']}
Services to choose from: {', '.join(task['candidates'])}

Trace telemetry per service and operation, before and after the incident start. A span named for a downstream call may be recorded by the calling service. Not every service necessarily reports spans.
```
{task['telemetry_table']}
```

One example trace from after the incident start (parents= gives each span's recorded parent):
```
{task['exemplar_trace']}
```

Respond with only a JSON object of this form:
{{"injected_fault_service": "...", "explanation": "...", "confidence": 0.0}}
confidence is your overall confidence between 0 and 1."""
    pairs = []
    for i, pair in enumerate(task["pairs"], 1):
        line = f"{i}. source_event_id={pair['source_event_id']} target_event_id={pair['target_event_id']} relationship: one of {', '.join(pair['allowed_relationships'])}"
        if pair["ancestor_required"]:
            line += " (include ancestor_event_id if you choose shared_ancestor)"
        pairs.append(line)
    return f"""You are helping investigate an incident in {SYSTEM_NAMES[task['system']]}.

Question: {task['question']}

Coverage of this export: PARTIAL. It came from a third-party tracing capture: some spans or dependency links of this execution may be missing from it, and some services may not report spans.
Each line is one span (event=span id), and parents= gives its recorded parent: those are the recorded dependency edges (parent -> this span).

{_REL_DEFS}

Span export:
```
{task['ai_visible_logs']}
```

Report exactly one claim per listed item:
{chr(10).join(pairs)}

Respond with only a JSON object of this form:
{{"claims": [{{"source_event_id": "...", "target_event_id": "...", "relationship": "...", "ancestor_event_id": null}}], "explanation": "...", "confidence": 0.0}}
confidence is your overall confidence between 0 and 1."""


def generate(data: Path) -> list[str]:
    manifest = json.loads((ROOT / "sample_manifest.json").read_text())
    index = pd.read_parquet(data / "cases.parquet")
    labels = manifest["labels"]
    for sub in ("tasks", "ground_truth", "extracts"):
        (ROOT / sub).mkdir(parents=True, exist_ok=True)
    produced, skipped = [], []

    def save(public: dict, private: dict, artifact: dict | None) -> None:
        (ROOT / "tasks" / f"{public['task_id']}.json").write_text(json.dumps(public, indent=1))
        (ROOT / "ground_truth" / f"{public['task_id']}.json").write_text(json.dumps(private, indent=1))
        if artifact is not None:
            (ROOT / "extracts" / f"{public['task_id']}.json").write_text(json.dumps(artifact))
        produced.append(public["task_id"])

    for dataset, cases in manifest["cases"].items():
        system = dataset.split("-")[1]
        known_roots = set(index[index.dataset.str.endswith(system)].root_cause_service)
        for case in cases:
            spans, inject_ms = load_case(data / case)
            candidates = sorted(known_roots | set(spans.serviceName))
            public, private = attribution_task(case, spans, inject_ms, labels[case], candidates)
            save(public, private, None)
            for family, built in (("reachability", reachability_task(case, spans, inject_ms)), ("gap", gap_task(case, spans))):
                if built is None:
                    skipped.append(f"{family}:{case}")
                else:
                    save(*built)
    (ROOT / "generation_log.json").write_text(json.dumps({"tasks": produced, "skipped_no_qualifying_trace": skipped}, indent=1))
    return produced
