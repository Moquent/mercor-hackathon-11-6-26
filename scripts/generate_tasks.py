#!/usr/bin/env python3
"""Generate the 12 public tasks and their separate private ground-truth files from captured runs,
then require the three independent reachability implementations to agree before writing anything."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.scenarios.ground_truth import closure
from app.scenarios.tasks import TASK_SPECS, build_task
from app.verifier.graph import build_dependency_dag
from app.verifier.reachability import get_descendants

BENCH = Path(__file__).resolve().parents[1] / "benchmark"


def main() -> None:
    artifacts = {sid: json.loads((BENCH / "runs" / f"{sid}.json").read_text()) for sid in {s["scenario_id"] for s in TASK_SPECS}}
    for sid, art in artifacts.items():
        if not art["integrity"]["valid"]:
            sys.exit(f"{sid}: refusing to generate tasks from a trace with integrity errors: {art['integrity']}")
        sql = {tuple(p) for p in art["sql_closure"]}
        bfs = closure(art["dependencies"])
        graph = build_dependency_dag(art["events"], art["dependencies"])
        nx_pairs = {(n, d) for n in graph for d in get_descendants(n, graph)}
        if not (sql == bfs == nx_pairs):
            sys.exit(f"{sid}: reachability oracles disagree (sql={len(sql)} bfs={len(bfs)} nx={len(nx_pairs)})")
        print(f"{sid}: SQL CTE, BFS and NetworkX closures agree on {len(sql)} reachable pairs")
    (BENCH / "tasks").mkdir(exist_ok=True)
    (BENCH / "ground_truth").mkdir(exist_ok=True)
    for spec in TASK_SPECS:
        public, private = build_task(spec, artifacts[spec["scenario_id"]])
        (BENCH / "tasks" / f"{spec['task_id']}.json").write_text(json.dumps(public, indent=1))
        (BENCH / "ground_truth" / f"{spec['task_id']}.json").write_text(json.dumps(private, indent=1))
        print(f"{spec['task_id']} [{public['coverage']}] {private['expected_answer']}")


if __name__ == "__main__":
    main()
