"""Adapter for the RCAEval benchmark (Pham et al., WWW 2025; MIT licence).

Maps real microservice traces onto DAGScope's event/edge model. Edges come only from each span's
recorded parentSpanID. Coverage is always open-world: this capture wasn't instrumented by us, and
a service can be absent from its traces altogether, so a missing edge proves nothing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from app.verifier.evidence import CoverageMetadata

REPO = "phamquiluan/RCAEval"
REVISION_FILE = "dataset_revision.txt"
OPEN_WORLD = CoverageMetadata(closed_world=False, level="partial", source="RCAEval trace capture (third-party instrumentation)",
                              notes="Not instrumented by DAGScope. Services can be absent from traces, so absence of an edge is not evidence.")


def select_cases(index: pd.DataFrame) -> dict[str, list[str]]:
    """The sample rule, fixed before any model ran: one case per root-cause service, repetition 1.
    RE3 takes each service's first fault; RE2 rotates through the sorted fault list by service."""
    picks: dict[str, list[str]] = {}
    for dataset in ("RE3-OB", "RE3-TT"):
        rows = index[(index.dataset == dataset) & (index.repetition == 1)].sort_values(["root_cause_service", "fault"])
        picks[dataset] = rows.groupby("root_cause_service").head(1).case.tolist()
    for dataset in ("RE2-OB", "RE2-TT"):
        rows = index[(index.dataset == dataset) & (index.repetition == 1)]
        faults = sorted(rows.fault.unique())
        chosen = []
        for i, service in enumerate(sorted(rows.root_cause_service.unique())):
            chosen.append(rows[(rows.root_cause_service == service) & (rows.fault == faults[i % len(faults)])].case.iloc[0])
        picks[dataset] = chosen
    return picks


def load_case(case_dir: Path) -> tuple[pd.DataFrame, int]:
    """Spans sorted by (startTime, spanID), plus the injection time in milliseconds."""
    spans = pd.read_parquet(case_dir / "traces.parquet")
    inject_s = int((case_dir / "inject_time.txt").read_text().strip())
    spans = spans.sort_values(["startTime", "spanID"]).reset_index(drop=True)
    return spans, inject_s * 1000


def status_failed(code: Any) -> bool:
    """gRPC: non-zero fails. HTTP: >= 400 fails. Absent means the span didn't report a status."""
    if pd.isna(code):
        return False
    code = int(code)
    return code >= 400 if code >= 100 else code != 0


def failed_mask(spans: pd.DataFrame) -> pd.Series:
    return spans.statusCode.map(status_failed).astype(bool)


def spans_to_records(spans: pd.DataFrame, case: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Span -> event, parentSpanID -> edge. Span ids serve as event ids and keep their original form."""
    events, deps = [], []
    failed = failed_mask(spans).tolist()
    columns = zip(spans.spanID, spans.traceID, spans.serviceName, spans.operationName, spans.startTime, spans.parentSpanID, spans.duration, spans.statusCode, failed)
    for span_id, trace_id, service, operation, start_us, parent, duration, code, is_failed in columns:
        has_parent = not pd.isna(parent) and parent != ""
        events.append({
            "event_id": span_id, "trace_id": trace_id, "request_id": None, "service": service, "event_type": operation,
            "timestamp": pd.Timestamp(int(start_us), unit="us", tz="UTC").isoformat(), "parent_event_ids": [parent] if has_parent else [],
            "message_id": None, "delivery_attempt_id": None, "database_transaction_id": None, "status": "failed" if is_failed else "ok",
            "metadata": {"label": trace_id[:6], "duration_us": None if pd.isna(duration) else int(duration), "status_code": None if pd.isna(code) else int(code)},
        })
        if has_parent:
            deps.append({"parent_event_id": parent, "child_event_id": span_id, "dependency_type": "request_propagation",
                         "evidence_reference": f"rcaeval:{case}:parentSpanID"})
    return events, deps


def render_spans(events: list[dict[str, Any]]) -> str:
    lines = []
    for e in sorted(events, key=lambda ev: (ev["timestamp"], ev["event_id"])):
        m = e["metadata"]
        lines.append(f"{e['timestamp']} [{e['service']}] event={e['event_id']} trace={e['trace_id']} op={e['event_type']} "
                     f"status={m['status_code'] if m['status_code'] is not None else '-'} duration_us={m['duration_us']} parents={','.join(e['parent_event_ids']) or '-'}")
    return "\n".join(lines)


def latency_table(spans: pd.DataFrame, inject_ms: int, limit: int = 40) -> str:
    """Before/after telemetry per service and operation, as an on-call engineer would pull it.
    Rows rank by size of change only, and the injection label isn't an input."""
    frame = spans.assign(after=spans.startTimeMillis >= inject_ms, failed=failed_mask(spans))
    rows = []
    for (service, operation), group in frame.groupby(["serviceName", "operationName"]):
        before, after = group[~group.after], group[group.after]
        med_b = before.duration.median() if len(before) else float("nan")
        med_a = after.duration.median() if len(after) else float("nan")
        ratio = float(med_a) / float(med_b) if med_b and med_b == med_b and med_a == med_a else 1.0
        err_a, err_b = int(after.failed.sum()), int(before.failed.sum())
        score = abs(ratio - 1.0) + (err_a - err_b) / max(len(after), 1) * 10 + abs(len(after) - len(before)) / max(len(before), 1)
        rows.append((score, service, operation, len(before), len(after), med_b, med_a, before.duration.quantile(0.95) if len(before) else float("nan"),
                     after.duration.quantile(0.95) if len(after) else float("nan"), err_b, err_a))
    rows.sort(key=lambda r: (-r[0], r[1], r[2]))
    fmt = lambda v: "-" if v != v else f"{float(v):.0f}"
    lines = ["service | operation | spans_before | spans_after | median_us_before | median_us_after | p95_us_before | p95_us_after | errors_before | errors_after"]
    for r in rows[:limit]:
        lines.append(f"{r[1]} | {r[2]} | {r[3]} | {r[4]} | {fmt(r[5])} | {fmt(r[6])} | {fmt(r[7])} | {fmt(r[8])} | {r[9]} | {r[10]}")
    if len(rows) > limit:
        lines.append(f"({len(rows) - limit} further operations with smaller changes omitted)")
    return "\n".join(lines)


def naive_error_blame(spans: pd.DataFrame, inject_ms: int) -> str | None:
    """Baseline: blame whichever service's spans carry the most new errors, else the biggest slowdown."""
    frame = spans.assign(after=spans.startTimeMillis >= inject_ms, failed=failed_mask(spans))
    errors = frame[frame.after & frame.failed].serviceName.value_counts()
    if len(errors):
        return str(sorted(errors[errors == errors.max()].index)[0])
    medians = frame.groupby(["serviceName", "after"]).duration.median().unstack()
    if True not in medians or False not in medians:
        return None
    ratio = (medians[True] / medians[False]).dropna()
    return str(ratio.sort_values(ascending=False).index[0]) if len(ratio) else None
