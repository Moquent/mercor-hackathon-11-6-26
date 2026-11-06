#!/usr/bin/env python3
"""Build the submission ZIP. Uses an allowlist of top-level paths, so a stray .env or venv can't slip in."""
from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ["README.md", "SUBMISSION.md", "DEMO.md", "Dockerfile", "docker-compose.yml", ".dockerignore", ".gitignore", ".env.example",
           "Makefile", "requirements.txt", "pytest.ini", "app", "benchmark", "scripts", "tests"]
SKIP_DIRS = {"__pycache__", ".pytest_cache", "live"}


def main() -> None:
    out = ROOT / "dist" / "dagscope-submission.zip"
    out.parent.mkdir(exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in INCLUDE:
            path = ROOT / name
            for file in ([path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())):
                if SKIP_DIRS & set(file.relative_to(ROOT).parts) or file.name == ".env" or file.suffix == ".pyc":
                    continue
                archive.write(file, Path("dagscope") / file.relative_to(ROOT))
                count += 1
    print(f"{out} ({count} files, {out.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
