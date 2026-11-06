#!/usr/bin/env python3
"""Does the test suite actually constrain the checker? Each mutation plants one realistic verifier
bug in a temporary copy of the code. A mutation the offline tests fail to catch is a test-suite gap."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V = "app/verifier"
MUTATIONS = [
    ("absence always counts as disproof (ignores coverage)", f"{V}/reachability.py", "    if coverage.closed_world:\n", "    if True:\n"),
    ("unknown coverage treated as complete", f"{V}/evidence.py", 'UNKNOWN_COVERAGE = CoverageMetadata(closed_world=False', 'UNKNOWN_COVERAGE = CoverageMetadata(closed_world=True'),
    ("integrity failures do not revoke closed-world", f"{V}/evidence.py", "        if cov.closed_world:\n", "        if False:\n"),
    ("reachability direction swapped", f"{V}/reachability.py", "    if nx.has_path(graph, source_event_id, target_event_id):\n", "    if nx.has_path(graph, target_event_id, source_event_id):\n"),
    ("timestamp proximity creates edges", f"{V}/graph.py", '    graph.graph["coverage"] = coverage\n',
     '    _o = sorted(event_rows, key=lambda e: str(e["timestamp"]))\n    for _a, _b in zip(_o, _o[1:]):\n        graph.add_edge(_a["event_id"], _b["event_id"], dependencies=[{"type": "temporal", "evidence_reference": ""}])\n    graph.graph["coverage"] = coverage\n'),
    ("any attempt gets credit for a commit", f"{V}/claims.py", 'same_attempt = s_node["delivery_attempt_id"] == t_node.get("delivery_attempt_id")', "same_attempt = True"),
    ("rolled-back transaction counts as committed", f"{V}/claims.py", 'committed = t_node.get("status") == COMMIT_STATUS', "committed = True"),
    ("retry edges count as production paths", f"{V}/claims.py", 'any(\n        d["type"] != "retry_dependency" for d in graph.edges[u, v]["dependencies"])', "True"),
    ("transitive path accepted as direct", f"{V}/claims.py", "    if graph.has_edge(source, target):\n", '    if base["dependency_path"]:\n        return {**base, "verdict": Verdict.SUPPORTED.value}\n    if graph.has_edge(source, target):\n'),
    ("named non-ancestor accepted as shared ancestor", f"{V}/claims.py", "    if chosen in shared:\n", "    if chosen in shared or (z and z in graph and nx.has_path(graph, z, x)):\n"),
    ("shared ancestry treated as direct reachability", f"{V}/reachability.py", "    if coverage.closed_world:\n        return make_result(claim, Verdict.CONTRADICTED,", "    if get_ancestors(source_event_id, graph) & get_ancestors(target_event_id, graph):\n        return make_result(claim, Verdict.SUPPORTED, 'shared', coverage=coverage, warnings=warnings)\n    if coverage.closed_world:\n        return make_result(claim, Verdict.CONTRADICTED,"),
    ("insufficient_evidence never accepted", f"{V}/claims.py", 'base["verdict"] = Verdict.SUPPORTED.value\n        base["explanation"] = "No path is visible and coverage is incomplete', 'base["verdict"] = Verdict.CONTRADICTED.value\n        base["explanation"] = "No path is visible and coverage is incomplete'),
    ("not_reachable accepted without closed-world", f"{V}/claims.py", 'base = _reachable_result(claim, graph, coverage)\n    base["claim"] = _claim_dict(claim)\n    flip = {', 'base = _reachable_result(claim, graph, coverage)\n    base["claim"] = _claim_dict(claim)\n    if base["verdict"] == Verdict.NOT_OBSERVED.value:\n        base["verdict"] = Verdict.SUPPORTED.value\n    flip = {'),
    ("hidden/missing ids are valid endpoints", f"{V}/reachability.py", 'return isinstance(event_id, str) and event_id in graph and not graph.nodes[event_id].get("missing")', "return isinstance(event_id, str) and event_id in graph"),
    ("any claimed service accepted as the injected one", f"{V}/intervention.py", "    if claimed_service == injected_service:\n", "    if True:\n"),
    ("ground-truth oracle ignores Postgres commit status", "app/scenarios/ground_truth.py", 'if row["commit_event_id"] == commit_event_id and artifact["state"]["xact_status"].get(str(row["xid"])) == "committed":', 'if row["commit_event_id"] == commit_event_id:'),
]


def main() -> int:
    results = []
    # Control first: an unmutated copy must pass, or every "caught" below would be meaningless.
    for description, rel_path, old, new in [("CONTROL (no mutation)", None, None, None)] + MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="dagscope-mut-") as tmp:
            work = Path(tmp)
            shutil.copytree(ROOT / "app", work / "app", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(ROOT / "tests", work / "tests", ignore=shutil.ignore_patterns("__pycache__"))
            for sub in ("runs", "tasks", "ground_truth", "rcaeval"):
                shutil.copytree(ROOT / "benchmark" / sub, work / "benchmark" / sub)
            shutil.copy(ROOT / "pytest.ini", work / "pytest.ini")
            if rel_path:
                target = work / rel_path
                source = target.read_text()
                if source.count(old) < 1:
                    sys.exit(f"mutation does not apply: {description}")
                target.write_text(source.replace(old, new, 1))
            proc = subprocess.run([sys.executable, "-m", "pytest", "--ignore=tests/test_end_to_end.py", "-q", "-p", "no:cacheprovider", "--no-header", "--tb=no"],
                                  cwd=work, capture_output=True, text=True, timeout=180)
            tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr.strip()[-200:]
            if rel_path is None:
                if proc.returncode != 0:
                    sys.exit(f"control run failed, so results would be meaningless: {tail}")
                print(f"CONTROL passes  [{tail}]")
                continue
            caught = proc.returncode == 1
            results.append({"mutation": description, "file": rel_path, "caught": caught, "pytest": tail})
            print(f"{'CAUGHT' if caught else 'MISSED'}  {description}  [{tail}]")
    caught = sum(r["caught"] for r in results)
    out = ROOT / "benchmark" / "results" / "mutation_check.json"
    out.write_text(json.dumps({"executed_at_utc": datetime.now(timezone.utc).isoformat(), "caught": caught, "total": len(results), "results": results}, indent=1))
    print(f"\n{caught}/{len(results)} planted verifier bugs caught by the offline tests")
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
