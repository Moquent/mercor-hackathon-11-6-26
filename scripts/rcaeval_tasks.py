#!/usr/bin/env python3
"""Generate the external RCAEval tasks from the fixed sample."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.external.rcaeval_bench import ROOT, generate

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--data", type=Path, default=Path.home() / ".cache" / "dagscope" / "rcaeval")
args = parser.parse_args()
if any((ROOT / "attempts").glob("*/*.json")):
    sys.exit("Refusing to regenerate: model attempts exist and quote these span ids.")
tasks = generate(args.data)
log = json.loads((ROOT / "generation_log.json").read_text())
print(Counter(t.split("_")[1] for t in tasks), len(tasks), "tasks")
print("skipped:", log["skipped_no_qualifying_trace"])
