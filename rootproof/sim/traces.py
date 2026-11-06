"""Illustrative request traces, sampled from the simulator's state at one second.

A trace follows one request from the entry point through the call graph. Each span's latency and failure come from
the simulator's rates at that second, so an error span marks where the run actually failed. The traces are for the
inspector only: the subject AI never sees them. Sampling is seeded, so re-runs match exactly.
"""
import random

LEAD_IN = 0.1                       # a caller spends this fraction of its own latency before calling its first dependency
JITTER_LOW, JITTER_HIGH = 0.85, 1.2  # per-span latency spread around the simulator's mean


def sample_trace(topo, rows_at, entry, rng):
    """One request, depth-first (parent before children). Returns the list of spans."""
    spans = []

    def call(svc, start, depth, attempts):
        state = rows_at[svc]
        own_ms = state["lat_ms"] * rng.uniform(JITTER_LOW, JITTER_HIGH)
        span = {"service": svc, "start_ms": round(start, 1), "duration_ms": 0.0, "status": "ok",
                "attempts": attempts, "depth": depth}
        spans.append(span)
        failed = rng.random() < state["err"]
        cursor = start + own_ms * LEAD_IN
        child_failed = False
        for child in topo[svc].deps:
            tries = 1
            while tries <= topo[svc].retries and rng.random() < rows_at[child]["seen_fail"]:
                tries += 1
            child_span = call(child, cursor, depth + 1, tries)
            cursor = child_span["start_ms"] + child_span["duration_ms"]
            child_failed = child_failed or child_span["status"] == "error"
            if topo[svc].mode == "fallback" and child_span["status"] == "ok":
                break
        span["duration_ms"] = round(max(own_ms, cursor - start), 1)
        fails = failed or (child_failed and topo[svc].mode == "all")
        span["status"] = "error" if fails else "ok"
        return span

    call(entry, 0.0, 0, 1)
    return spans


def sample_traces(topo, rows, entry, second, n=4, seed="0"):
    """n sampled requests at one second. Traces that contain an error come first."""
    rows_at = {r["svc"]: r for r in rows if r["t"] == second}
    out = []
    for i in range(n):
        spans = sample_trace(topo, rows_at, entry, random.Random(f"{seed}-{second}-{i}"))
        out.append({"second": second, "index": i, "spans": spans,
                    "failed": any(s["status"] == "error" for s in spans)})
    return sorted(out, key=lambda t: (not t["failed"], t["index"]))
