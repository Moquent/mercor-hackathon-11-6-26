"""Prepare RCAEval RE2-OB cases (real fault injections on Online Boutique) for the subject AI and the verifier.

Needs pandas+pyarrow (run once):  python rootproof/rcaeval_prepare.py data/rcaeval
Writes tasks/rcaeval/<case>/prompt.txt (what the AI sees) and features.json (minute-level series the verifier uses, stdlib only).
The fault label is only in the case name and never appears in the prompt.
"""
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICES = ["frontend", "adservice", "cartservice", "checkoutservice", "currencyservice", "emailservice",
            "paymentservice", "productcatalogservice", "recommendationservice", "redis", "shippingservice"]
METRICS = ["cpu", "mem", "diskio", "socket", "workload", "error", "latency-90"]
BEFORE, AFTER = 8, 5   # minutes shown before / after the injection; paged at injection + AFTER minutes

DEPS = """frontend -> adservice, cartservice, checkoutservice, currencyservice, productcatalogservice, recommendationservice, shippingservice
checkoutservice -> cartservice, currencyservice, emailservice, paymentservice, productcatalogservice, shippingservice
recommendationservice -> productcatalogservice
cartservice -> redis"""

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


def prep(case_dir):
    name = os.path.basename(case_dir.rstrip("/"))
    inject = int(open(os.path.join(case_dir, "inject_time.txt")).read().strip())
    m = pd.read_parquet(os.path.join(case_dir, "metrics.parquet"))
    lg = pd.read_parquet(os.path.join(case_dir, "logs.parquet"))
    t0, t1 = inject - BEFORE * 60, inject + AFTER * 60
    m = m[(m.time >= t0) & (m.time < t1)].copy()
    m["minute"] = (m.time - t0) // 60
    g = m.groupby("minute").mean(numeric_only=True)
    feats, lines = {}, []
    clock = lambda k: datetime.fromtimestamp(t0 + 60 * k, timezone.utc).strftime("%H:%M")
    for s in SERVICES:
        feats[s] = {}
        cols = [(mt, f"{s}_{mt}") for mt in METRICS if f"{s}_{mt}" in g.columns]
        if not cols:
            continue
        lines.append(f"\n### {s}")
        for mt, c in cols:
            if mt == "mem":
                g[c] = g[c] / 1e6
            feats[s][mt] = [None if pd.isna(v) else round(float(v), 3) for v in g[c].tolist()]
        for k in range(len(g)):
            cells = []
            for mt, c in cols:
                v = g[c].iloc[k]
                label = {"workload": "rps", "latency-90": "p90", "error": "err"}.get(mt, mt)
                cells.append(f"{label}={'' if pd.isna(v) else f'{v:.1f}'}")
            lines.append(f"{clock(k)}  " + "  ".join(cells))
    lg = lg[(lg.timestamp >= t0) & (lg.timestamp < t1)]
    bad = lg[lg.message.str.contains("error|fail|exception|timeout|refused|unavailable|deadline", case=False, regex=True)].copy()
    bad["minute"] = (bad.timestamp - t0) // 60
    counts = bad.groupby(["container_name", "minute"]).size().unstack(fill_value=0)
    log_lines = ["service                 " + " ".join(clock(k) for k in range(BEFORE + AFTER))]
    log_feats = {}
    for svc in counts.index:
        row = [int(counts.loc[svc].get(k, 0)) for k in range(BEFORE + AFTER)]
        log_feats[svc] = row
        log_lines.append(f"{svc:23s} " + " ".join(f"{v:5d}" for v in row))
    samples = bad.groupby("container_name").message.first()
    log_lines += ["", "sample messages:"] + [f"  {k}: {v[:160]}" for k, v in samples.items()]
    _, svc, fault, _ = name.split("_")
    paged = datetime.fromtimestamp(t1, timezone.utc).strftime("%H:%M")
    prompt = PROMPT.format(paged=paged, deps=DEPS, metrics="\n".join(lines), logs="\n".join(log_lines))
    out = os.path.join(ROOT, "tasks", "rcaeval", name)
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, "prompt.txt"), "w").write(prompt)
    json.dump({"case": name, "truth_service": svc, "truth_fault": fault, "inject_minute": BEFORE, "minutes": BEFORE + AFTER,
               "metrics": feats, "error_logs": log_feats}, open(os.path.join(out, "features.json"), "w"))
    return name, len(prompt)


if __name__ == "__main__":
    base = sys.argv[1]
    for d in sorted(os.listdir(base)):
        if d.startswith("re2ob_"):
            print(*prep(os.path.join(base, d)))
