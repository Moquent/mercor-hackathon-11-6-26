#!/usr/bin/env python3
"""Execute the failure scenarios through the live services and save the captured evidence."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.scenarios.runner import SCENARIOS, run_and_save


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="benchmark/runs", help="output dir (use benchmark/live to keep the canonical benchmark)")
    parser.add_argument("--only", nargs="*", choices=list(SCENARIOS))
    parser.add_argument("--force", action="store_true", help="overwrite the canonical runs even though model attempts exist")
    args = parser.parse_args()
    # Attempts quote event ids from the canonical run; replacing it would orphan them.
    attempts = [p for p in Path("benchmark/model_attempts").glob("*/") if not p.name.startswith("HANDCRAFTED")]
    if Path(args.out).resolve() == Path("benchmark/runs").resolve() and attempts and not args.force:
        sys.exit(f"Refusing to replace canonical runs: model attempts exist in {[p.name for p in attempts]}. Use --out benchmark/live for a new run.")
    for sid, art in run_and_save(Path(args.out), args.only).items():
        i = art["integrity"]
        print(f"{sid}: run={art['run_id']} events={i['event_count']} edges={i['edge_count']} integrity_valid={i['valid']} interventions={len(art.get('interventions', []))}")


if __name__ == "__main__":
    main()
