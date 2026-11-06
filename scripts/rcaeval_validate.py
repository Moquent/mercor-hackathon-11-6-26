#!/usr/bin/env python3
"""Stress the DAG engine on real traces: integrity, an independent oracle, and masking soundness.

Masking soundness is a metamorphic property that needs no model and no human labels. After hiding
10% of the spans: anything SUPPORTED must be truly reachable in the full graph, nothing may be
CONTRADICTED, and a negative claim may never be SUPPORTED.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.external.rcaeval import OPEN_WORLD, load_case, spans_to_records
from app.scenarios.ground_truth import adjacency, bfs_path
from app.verifier.claims import verify_claim
from app.verifier.evidence import CoverageMetadata
from app.verifier.graph import build_dependency_dag, validate_graph_integrity
from app.verifier.reachability import get_descendants, verify_reachability

OUT = Path(__file__).resolve().parents[1] / "benchmark" / "rcaeval"
SEED = 20261106
CLAIMED_COMPLETE = CoverageMetadata(closed_world=True, level="complete", source="deliberately false claim in validation")


def oracle_descendants(adj: dict[str, list[str]], start: str) -> set[str]:
    stack, seen = list(adj.get(start, [])), set()
    while stack:
        node = stack.pop()
        if node not in seen:
            seen.add(node)
            stack.extend(adj.get(node, []))
    return seen


def validate_case(data: Path, case: str, starts: int, pairs: int) -> dict:
    began = time.perf_counter()
    spans, _ = load_case(data / case)
    events, deps = spans_to_records(spans, case)
    graph = build_dependency_dag(events, deps, OPEN_WORLD)
    integrity = validate_graph_integrity(graph, events)
    build_s = time.perf_counter() - began
    rng = random.Random(SEED)
    adj = adjacency(deps)
    known = {e["event_id"] for e in events}

    # 1. NetworkX vs independent DFS, exact descendant sets from sampled start spans.
    disagreements = 0
    with_children = sorted(adj)
    sample = rng.sample(with_children, min(starts, len(with_children)))
    for start in sample:
        disagreements += get_descendants(start, graph) != oracle_descendants(adj, start)

    # 2. Masking soundness, using pairs that share a trace so true paths are common.
    by_trace: dict[str, list[str]] = {}
    for e in events:
        by_trace.setdefault(e["trace_id"], []).append(e["event_id"])
    hidden = {e["event_id"] for e in events if rng.random() < 0.10}
    masked_events = [e for e in events if e["event_id"] not in hidden]
    masked_deps = [d for d in deps if d["parent_event_id"] not in hidden and d["child_event_id"] not in hidden]
    masked = build_dependency_dag(masked_events, masked_deps, OPEN_WORLD)
    traces = [t for t, ids in sorted(by_trace.items()) if len(ids) >= 3]
    counts = {"pairs": 0, "supported": 0, "not_observed": 0, "true_path_hidden": 0, "false_support": 0, "wrongly_contradicted": 0, "negative_accepted": 0}
    while counts["pairs"] < pairs:
        ids = [i for i in by_trace[rng.choice(traces)] if i not in hidden]
        if len(ids) < 2:
            continue
        source, target = rng.sample(ids, 2)
        counts["pairs"] += 1
        truly = bfs_path(adj, source, target) is not None
        verdict = verify_reachability(source, target, masked)["verdict"]
        if verdict == "SUPPORTED":
            counts["supported"] += 1
            counts["false_support"] += not truly
        elif verdict == "NOT_OBSERVED":
            counts["not_observed"] += 1
            counts["true_path_hidden"] += truly
        elif verdict == "CONTRADICTED":
            counts["wrongly_contradicted"] += 1
        negative = verify_claim({"source_event_id": source, "target_event_id": target, "relationship": "not_reachable"}, masked)["verdict"]
        counts["negative_accepted"] += negative == "SUPPORTED"

    # 3. A false claim of completeness on a trace with real gaps must be revoked, whoever asserts it.
    revocation = None
    if integrity["dangling_references"]:
        full_claimed = build_dependency_dag(events, deps, CLAIMED_COMPLETE)
        ids = sorted(known)
        result = verify_reachability(ids[0], ids[-1], full_claimed)
        revocation = {"verdict": result["verdict"], "closed_world_after": result["coverage"]["closed_world"]}
        if result["dependency_path"]:
            revocation["note"] = "pair happened to be connected"

    return {
        "case": case, "spans": integrity["event_count"], "edges": integrity["edge_count"], "traces": len(by_trace),
        "dangling_edges": len(integrity["dangling_references"]), "cycles": len(integrity["cycles"]), "duplicate_ids": len(integrity["duplicate_event_ids"]),
        "parent_list_mismatches": len(integrity["parent_list_mismatches"]), "clock_skew_warnings": len(integrity["clock_skew_warnings"]),
        "oracle_starts_checked": len(sample), "oracle_disagreements": disagreements, "masking": counts, "completeness_revocation": revocation,
        "seconds": round(time.perf_counter() - began, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path.home() / ".cache" / "dagscope" / "rcaeval")
    parser.add_argument("--starts", type=int, default=2000)
    parser.add_argument("--pairs", type=int, default=1000)
    args = parser.parse_args()
    manifest = json.loads((OUT / "sample_manifest.json").read_text())
    rows = []
    for group in manifest["cases"].values():
        for case in group:
            row = validate_case(args.data, case, args.starts, args.pairs)
            rows.append(row)
            print(json.dumps({k: row[k] for k in ("case", "spans", "dangling_edges", "cycles", "oracle_disagreements", "masking", "completeness_revocation", "seconds")}), flush=True)
    keys = ("false_support", "wrongly_contradicted", "negative_accepted")
    totals = {
        "cases": len(rows), "spans": sum(r["spans"] for r in rows), "edges": sum(r["edges"] for r in rows), "dangling_edges": sum(r["dangling_edges"] for r in rows),
        "cases_with_gaps": sum(r["dangling_edges"] > 0 for r in rows), "cycles": sum(r["cycles"] for r in rows), "oracle_starts_checked": sum(r["oracle_starts_checked"] for r in rows),
        "oracle_disagreements": sum(r["oracle_disagreements"] for r in rows), "masked_pairs": sum(r["masking"]["pairs"] for r in rows),
        "true_paths_hidden_by_mask": sum(r["masking"]["true_path_hidden"] for r in rows), **{k: sum(r["masking"][k] for r in rows) for k in keys},
        "revocations_checked": sum(r["completeness_revocation"] is not None for r in rows),
        "revocations_failed": sum(bool(r["completeness_revocation"] and r["completeness_revocation"]["closed_world_after"]) for r in rows),
    }
    (OUT / "results").mkdir(parents=True, exist_ok=True)
    (OUT / "results" / "verifier_on_real_traces.json").write_text(json.dumps({"executed_at_utc": datetime.now(timezone.utc).isoformat(), "seed": SEED, "revision": manifest["revision"], "totals": totals, "cases": rows}, indent=1))
    print(json.dumps(totals, indent=1))
    return 0 if not (totals["oracle_disagreements"] or totals["revocations_failed"] or any(totals[k] for k in keys)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
