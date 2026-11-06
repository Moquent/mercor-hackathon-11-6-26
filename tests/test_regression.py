"""Regression gate: the refactor must not change any verdict, metric or saved prompt.

Runs the full evaluation in a throwaway copy of the repo with .env removed, so every
Jev call must be served from results/jev_cache.json (offline, no API keys).

    python3 -m unittest tests/test_regression.py -v

Set RP_EVAL_CMD to override the entrypoint (default: the current evaluate script).
"""
import glob
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(REPO, "tests", "golden")
DEFAULT_CMD = f"{shlex.quote(sys.executable)} rootproof/evaluate.py"
SKIP = shutil.ignore_patterns(".env", "data", "__pycache__", "*.zip", ".git", "*.pdf")


def is_subset(golden, actual, path="$"):
    """Every golden value must appear unchanged in actual. Extra keys are allowed (additive refactor)."""
    if isinstance(golden, dict):
        for k, v in golden.items():
            if k not in actual:
                return f"{path}.{k} missing"
            err = is_subset(v, actual[k], f"{path}.{k}")
            if err:
                return err
        return None
    if isinstance(golden, list):
        if len(golden) != len(actual):
            return f"{path} length {len(golden)} != {len(actual)}"
        for i, (g, a) in enumerate(zip(golden, actual)):
            err = is_subset(g, a, f"{path}[{i}]")
            if err:
                return err
        return None
    if golden != actual:
        return f"{path}: {golden!r} != {actual!r}"
    return None


def read_json(path):
    with open(path) as f:
        return json.load(f)


def prompt_hashes(root):
    out = {}
    for p in sorted(glob.glob(os.path.join(root, "tasks", "**", "prompt.txt"), recursive=True)):
        with open(p, "rb") as f:
            out[os.path.relpath(p, root)] = hashlib.sha256(f.read()).hexdigest()
    return out


class GateSelfCheck(unittest.TestCase):
    def test_detects_changed_value(self):
        golden = {"verdicts": [{"verdict": "ACCEPT", "err": 0.0}]}
        self.assertIsNotNone(is_subset(golden, {"verdicts": [{"verdict": "PARTIAL", "err": 0.0}]}))

    def test_allows_added_fields(self):
        golden = {"stats": {"tasks": 15}}
        self.assertIsNone(is_subset(golden, {"stats": {"tasks": 15, "new_metric": 0.5}}))


class RegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rp-regress-")
        copy = os.path.join(cls.tmp, "repo")
        shutil.copytree(REPO, copy, ignore=SKIP)
        cmd = shlex.split(os.environ.get("RP_EVAL_CMD", DEFAULT_CMD))
        proc = subprocess.run(cmd, cwd=copy, capture_output=True, text=True, timeout=900)
        cls.proc = proc
        cls.copy = copy

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_evaluation_runs_offline(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-2000:])

    def test_summary_matches_golden(self):
        golden = read_json(os.path.join(GOLDEN, "summary.json"))
        actual = read_json(os.path.join(self.copy, "results", "summary.json"))
        err = is_subset(golden, actual)
        self.assertIsNone(err, err)

    def test_prompts_unchanged(self):
        golden = read_json(os.path.join(GOLDEN, "prompt_hashes.json"))
        actual = prompt_hashes(self.copy)
        for path, digest in golden.items():
            self.assertEqual(actual.get(path), digest, f"prompt changed: {path}")

    def test_no_new_jev_calls_without_keys(self):
        cache_before = read_json(os.path.join(REPO, "results", "jev_cache.json"))
        cache_after = read_json(os.path.join(self.copy, "results", "jev_cache.json"))
        self.assertEqual(set(cache_before), set(cache_after), "evaluation needed Jev calls that are not cached")


if __name__ == "__main__":
    unittest.main()
