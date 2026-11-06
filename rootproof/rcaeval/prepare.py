"""Prepares RCAEval RE2-OB cases: the packet the subject AI sees, and the minute-level series the verifier uses.

Needs pandas and pyarrow (only for this step):  python3 -m rootproof prepare-rcaeval data/rcaeval
The fault label lives only in the case name. It never appears in a prompt.
"""
import json
import os
from datetime import datetime, timezone

from ..config import RCAEVAL_TASKS
from .topology import SERVICES, deps_text

METRICS = ["cpu", "mem", "diskio", "socket", "workload", "error", "latency-90"]
BEFORE, AFTER = 8, 5   # minutes shown before and after the injection; paged at injection + AFTER minutes
MEM_SCALE = 1e6        # memory is exported in bytes; the packet shows MB
LOG_ERROR_PATTERN = "error|fail|exception|timeout|refused|unavailable|deadline"

PROMPT = """You are the on-call SRE for an e-commerce site (Online Boutique microservices on Kubernetes). You were paged at {paged} UTC
because user-facing latency/errors look abnormal. Using ONLY the telemetry below, find the root cause.

## Service dependency map (caller -> callees)
{deps}

## Per-service metrics, 1-minute means
cpu = CPU %, mem = memory MB, diskio = disk IO, socket = open sockets, rps = requests/s, err = errors/s, p90 = p90 latency (exported units)
(blank = metric not collected for that service)
{metrics}

## Error/warning log lines per service per minute
{logs}

End your answer with a JSON block exactly like:
```json
{{"root_cause_service": "...", "fault_type": "cpu|mem|disk|delay|loss|socket", "propagation_path": ["root service", "...", "frontend"],
 "actions": ["..."]}}
```"""

LABELS = {"workload": "rps", "latency-90": "p90", "error": "err"}


def _metric_lines(g, clock):
    """Packet lines per service, plus the per-metric series the verifier reads."""
    lines, feats = [], {}
    for s in SERVICES:
        feats[s] = {}
        cols = [(mt, f"{s}_{mt}") for mt in METRICS if f"{s}_{mt}" in g.columns]
        if not cols:
            continue
        lines.append(f"\n### {s}")
        for mt, c in cols:
            if mt == "mem":
                g[c] = g[c] / MEM_SCALE
            feats[s][mt] = [None if _isnull(v) else round(float(v), 3) for v in g[c].tolist()]
        for k in range(len(g)):
            cells = [f"{LABELS.get(mt, mt)}={'' if _isnull(g[c].iloc[k]) else f'{g[c].iloc[k]:.1f}'}" for mt, c in cols]
            lines.append(f"{clock(k)}  " + "  ".join(cells))
    return lines, feats


def _isnull(v):
    return v != v   # NaN is the only value not equal to itself, which keeps pandas out of this module's imports


def _log_lines(logs, t0, clock):
    bad = logs[logs.message.str.contains(LOG_ERROR_PATTERN, case=False, regex=True)].copy()
    bad["minute"] = (bad.timestamp - t0) // 60
    counts = bad.groupby(["container_name", "minute"]).size().unstack(fill_value=0)
    lines = ["service                 " + " ".join(clock(k) for k in range(BEFORE + AFTER))]
    series = {}
    for svc in counts.index:
        row = [int(counts.loc[svc].get(k, 0)) for k in range(BEFORE + AFTER)]
        series[svc] = row
        lines.append(f"{svc:23s} " + " ".join(f"{v:5d}" for v in row))
    samples = bad.groupby("container_name").message.first()
    lines += ["", "sample messages:"] + [f"  {k}: {v[:160]}" for k, v in samples.items()]
    return lines, series


def prepare_case(case_dir, out_root=RCAEVAL_TASKS):
    import pandas as pd

    name = os.path.basename(case_dir.rstrip("/"))
    with open(os.path.join(case_dir, "inject_time.txt")) as f:
        inject = int(f.read().strip())
    metrics = pd.read_parquet(os.path.join(case_dir, "metrics.parquet"))
    logs = pd.read_parquet(os.path.join(case_dir, "logs.parquet"))
    t0, t1 = inject - BEFORE * 60, inject + AFTER * 60

    metrics = metrics[(metrics.time >= t0) & (metrics.time < t1)].copy()
    metrics["minute"] = (metrics.time - t0) // 60
    grouped = metrics.groupby("minute").mean(numeric_only=True)
    clock = lambda k: datetime.fromtimestamp(t0 + 60 * k, timezone.utc).strftime("%H:%M")

    metric_lines, feats = _metric_lines(grouped, clock)
    logs = logs[(logs.timestamp >= t0) & (logs.timestamp < t1)]
    log_lines, log_series = _log_lines(logs, t0, clock)

    _, svc, fault, _ = name.split("_")
    paged = datetime.fromtimestamp(t1, timezone.utc).strftime("%H:%M")
    prompt = PROMPT.format(paged=paged, deps=deps_text(), metrics="\n".join(metric_lines), logs="\n".join(log_lines))

    out = os.path.join(out_root, name)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "prompt.txt"), "w") as f:
        f.write(prompt)
    with open(os.path.join(out, "features.json"), "w") as f:
        json.dump({"case": name, "truth_service": svc, "truth_fault": fault, "inject_minute": BEFORE, "minutes": BEFORE + AFTER,
                   "metrics": feats, "error_logs": log_series}, f)
    return name, len(prompt)


def prepare_all(base):
    for d in sorted(os.listdir(base)):
        if d.startswith("re2ob_"):
            print(*prepare_case(os.path.join(base, d)))
