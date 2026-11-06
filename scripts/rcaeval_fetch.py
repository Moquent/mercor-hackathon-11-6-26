#!/usr/bin/env python3
"""Download the fixed RCAEval sample at a pinned dataset revision (telemetry only, not the 3.44 GB suite)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from huggingface_hub import HfApi, hf_hub_download, snapshot_download

from app.external.rcaeval import REPO, select_cases

MANIFEST = Path(__file__).resolve().parents[1] / "benchmark" / "rcaeval" / "sample_manifest.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path.home() / ".cache" / "dagscope" / "rcaeval")
    args = parser.parse_args()
    revision = json.loads(MANIFEST.read_text())["revision"] if MANIFEST.exists() else HfApi().dataset_info(REPO).sha
    args.data.mkdir(parents=True, exist_ok=True)
    index = pd.read_parquet(hf_hub_download(REPO, "cases.parquet", repo_type="dataset", revision=revision, local_dir=args.data))
    picks = select_cases(index)
    cases = [c for group in picks.values() for c in group]
    snapshot_download(REPO, repo_type="dataset", revision=revision, allow_patterns=[f"{c}/*" for c in cases], local_dir=args.data)
    rows = index.set_index("case").loc[cases]
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({"dataset": REPO, "revision": revision, "licence": "MIT", "rule": "app.external.rcaeval.select_cases", "cases": picks,
                                    "labels": {c: {"root_cause_service": rows.loc[c, "root_cause_service"], "fault": rows.loc[c, "fault"], "fault_description": rows.loc[c, "fault_description"]} for c in cases}}, indent=1))
    print(f"revision {revision}: {len(cases)} cases in {args.data}")
    for dataset, group in picks.items():
        print(dataset, group)


if __name__ == "__main__":
    main()
