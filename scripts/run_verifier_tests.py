#!/usr/bin/env python3
"""Run the checker's test suite and write a summary containing only what actually executed."""
from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmark" / "results"


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    xml_path = RESULTS / "verifier_tests.junit.xml"
    code = subprocess.run([sys.executable, "-m", "pytest", f"--junitxml={xml_path}", *sys.argv[1:]], cwd=ROOT).returncode
    per_file: dict[str, Counter] = {}
    for case in ET.parse(xml_path).getroot().iter("testcase"):
        outcome = "failed" if case.find("failure") is not None or case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed"
        per_file.setdefault(case.get("classname", "").split(".")[-1], Counter())[outcome] += 1
    totals = sum(per_file.values(), Counter())
    summary = {"executed_at_utc": datetime.now(timezone.utc).isoformat(), "passed": totals["passed"], "failed": totals["failed"], "skipped": totals["skipped"],
               "per_file": {k: dict(v) for k, v in sorted(per_file.items())}}
    (RESULTS / "verifier_tests.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
