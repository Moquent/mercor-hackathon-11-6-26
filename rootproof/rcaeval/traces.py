"""Per-edge call statistics from the real RCAEval traces, for display only (the AI never sees them).

For every call (parent span service -> child span service) it records, per minute on the same clock as the packet:
the number of calls and the p90 latency. The trace export carries no error status (every span reports status 0), so
error counts are recorded as unavailable rather than guessed.
"""
import json
import os

from ..config import RCAEVAL_DATA, RCAEVAL_TASKS
from .prepare import AFTER, BEFORE

MICROSECONDS_PER_MS = 1000.0
SPAN_NAME_FIXES = {"frontendservice": "frontend"}


def _service(name):
    return SPAN_NAME_FIXES.get(name, name)


def aggregate_case(case_dir, out_path):
    import pandas as pd

    with open(os.path.join(case_dir, "inject_time.txt")) as f:
        inject = int(f.read().strip())
    t0 = inject - BEFORE * 60
    spans = pd.read_parquet(os.path.join(case_dir, "traces.parquet"),
                            columns=["traceID", "spanID", "parentSpanID", "serviceName", "startTimeMillis", "duration"])
    parent_names = spans[["traceID", "spanID", "serviceName"]].drop_duplicates(["traceID", "spanID"])
    parent_names = parent_names.rename(columns={"spanID": "parentSpanID", "serviceName": "parentService"})
    children = spans[spans.parentSpanID.notna() & (spans.parentSpanID != "")].copy()
    children = children.merge(parent_names, on=["traceID", "parentSpanID"], how="left")
    children["caller"] = [_service(p) if isinstance(p, str) else None for p in children.parentService]
    children["callee"] = [_service(c) for c in children.serviceName]
    children = children[children.caller.notna() & (children.caller != children.callee)]
    children["minute"] = (children.startTimeMillis // 1000 - t0) // 60
    children = children[(children.minute >= 0) & (children.minute < BEFORE + AFTER)]
    children["ms"] = children.duration / MICROSECONDS_PER_MS
    edges = []
    for (caller, callee), g in children.groupby(["caller", "callee"]):
        counts = g.groupby("minute").size()
        p90 = g.groupby("minute").ms.quantile(0.9)
        calls = [int(counts.get(m, 0)) for m in range(BEFORE + AFTER)]
        latency = [round(float(p90[m]), 2) if m in p90.index else None for m in range(BEFORE + AFTER)]
        edges.append({"from": caller, "to": callee, "calls": calls, "p90_ms": latency})
    edges.sort(key=lambda e: (e["from"], e["to"]))
    with open(out_path, "w") as f:
        json.dump({"minutes": BEFORE + AFTER, "errors_recorded": False, "edges": edges}, f)
    return len(edges)


def aggregate_all(instances=("1", "3")):
    done = 0
    for case in sorted(os.listdir(RCAEVAL_TASKS)):
        if not case.endswith(tuple(f"_{i}" for i in instances)):
            continue
        case_dir = os.path.join(RCAEVAL_DATA, case)
        if not os.path.exists(os.path.join(case_dir, "traces.parquet")):
            continue
        aggregate_case(case_dir, os.path.join(RCAEVAL_TASKS, case, "traces.json"))
        done += 1
    print(f"aggregated traces for {done} cases")
    return 0
