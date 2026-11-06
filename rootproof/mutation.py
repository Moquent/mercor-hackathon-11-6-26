"""Does the test suite actually constrain the verifier? Each mutation plants one realistic verifier bug in a temporary
copy of the code and runs the fast offline tests (tests/test_verifier.py). A mutation the tests miss is a test gap.

    python3 -m rootproof mutate

An unmutated CONTROL copy runs first and must pass, or every "caught" below would be meaningless.
Writes results/mutation_check.json.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .config import RESULTS, ROOT

V = "rootproof/verify"
TEST = "tests/test_verifier.py"
COPY = ("rootproof", "tests", "tasks", "attempts", "results")
TIMEOUT_S = 300
WORKERS = 5

# (description, file, exact old text, new text). Each is one bug a plausible refactor could introduce.
MUTATIONS = [
    ("SLO judged from page time (JUDGE_WINDOW starts at 300)", "rootproof/config.py",
     "JUDGE_WINDOW = (450, 600)", "JUDGE_WINDOW = (300, 600)"),
    ("SLO threshold 0.5 instead of 0.05", "rootproof/config.py",
     "SLO_ERROR_RATE = 0.05", "SLO_ERROR_RATE = 0.5"),
    ("Jev threshold 0.1: weak mentions count as claimed actions", "rootproof/config.py",
     "JEV_THRESHOLD = 0.5", "JEV_THRESHOLD = 0.1"),
    ("seed sweep ignored: every seed copies the seed-0 result", f"{V}/verdict.py",
     "by_seed = [resolves(incident, list(ex.actions), s) for s in SEEDS]", "by_seed = [fixed for s in SEEDS]"),
    ("resolves_on_all_seeds only checks seed 0", f"{V}/replay.py",
     "return all(resolves(incident, action_keys, s) for s in SEEDS)", "return resolves(incident, action_keys, 0)"),
    ("story checks ignored: a working fix alone is ACCEPT", f"{V}/verdict.py",
     'verdict = "ACCEPT" if fixed and story_ok else', 'verdict = "ACCEPT" if fixed else'),
    ("no-outage incidents accept production changes", f"{V}/verdict.py",
     'verdict="REJECT" if prod_actions else "ACCEPT"', 'verdict="ACCEPT"'),
    ("origin check always passes (victims accepted as roots)", f"{V}/claims.py",
     'checks.append(Check("origin", not victim,', 'checks.append(Check("origin", True,'),
    ("temporal check always passes", f"{V}/claims.py",
     "ok = t_inc is None or t_svc <= t_inc + TEMPORAL_SLACK_S", "ok = True"),
    ("baseline check disabled (pre-existing noise blamed)", f"{V}/claims.py",
     "pre_existing = pre > 0.05 and abs(during - pre) < 0.05", "pre_existing = False"),
    ("JSON root ignored: only Jev's single choice is checked", f"{V}/extract.py",
     'named = str((block or {}).get("root_cause_service", ""))', 'named = ""'),
    ("causal edge direction swapped in the true graph", f"{V}/chain.py",
     "edges.add((x, y))", "edges.add((y, x))"),
    ("true graph without load (retry/fallback) edges", f"{V}/chain.py",
     'amplified = topo[x].deps if topo[x].retries or topo[x].mode == "fallback" else []', "amplified = []"),
    ("ungrounded claims counted as verified", f"{V}/trace.py",
     '"UNGROUNDED" if not g else', '"VERIFIED" if not g else'),
    ("cited-step support not required (any step counts)", f"{V}/trace.py",
     "_supported(c, cited)", "_supported(c, steps)"),
    ("link faults treated as callee failures", f"{V}/trace.py",
     'calls and (callee["erroring"] or callee["slow"]) and caller["erroring"]', 'calls and caller["erroring"]'),
    ("RCAEval signature check always passes", "rootproof/rcaeval/checks.py",
     'return Check("signature", zmax > Z,', 'return Check("signature", True,'),
    ("RCAEval baseline window leaks into the incident", "rootproof/rcaeval/checks.py",
     "BASELINE_MINUTES = 2 ", "BASELINE_MINUTES = -2 "),
    ("RCAEval path hop direction swapped", "rootproof/rcaeval/checks.py",
     "b not in CALLERS.get(a, [])", "a not in CALLERS.get(b, [])"),
]

_FAIL_RE = re.compile(r"^(?:FAIL|ERROR): (\S+) \((\S+)\)", re.M)


def _env():
    """No API keys: any Jev cache miss must fail instead of reaching the network."""
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI", "OPENAI_API_KEY", "TYPESAFE", "TYPESAFE_API_KEY")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_one(mutation):
    description, rel_path, old, new = mutation
    with tempfile.TemporaryDirectory(prefix="rootproof-mut-") as tmp:
        for d in COPY:
            shutil.copytree(os.path.join(ROOT, d), os.path.join(tmp, d), ignore=shutil.ignore_patterns("__pycache__"))
        if rel_path:
            target = os.path.join(tmp, rel_path)
            with open(target) as f:
                src = f.read()
            if src.count(old) != 1:
                return {"mutation": description, "file": rel_path, "caught": None,
                        "first_failure": f"mutation does not apply ({src.count(old)} matches)"}
            with open(target, "w") as f:
                f.write(src.replace(old, new, 1))
        try:
            proc = subprocess.run([sys.executable, "-m", "unittest", TEST], cwd=tmp, env=_env(),
                                  capture_output=True, text=True, timeout=TIMEOUT_S)
            out, code = proc.stderr + proc.stdout, proc.returncode
        except subprocess.TimeoutExpired:
            out, code = "timeout", -1
    m = _FAIL_RE.search(out)
    first = ".".join(m.group(2).split(".")[-2:]) if m else (out.strip().splitlines() or [""])[-1][-160:]
    return {"mutation": description, "file": rel_path, "caught": code != 0, "first_failure": None if code == 0 else first}


def main():
    t0 = time.time()
    control = run_one(("CONTROL (no mutation)", None, None, None))
    if control["caught"]:
        print(f"CONTROL fails, so results would be meaningless: {control['first_failure']}")
        return 1
    print(f"CONTROL passes ({time.time() - t0:.0f}s)")
    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(run_one, MUTATIONS))
    caught = sum(r["caught"] is True for r in results)
    width = max(len(r["mutation"]) for r in results)
    for r in results:
        tag = "CAUGHT" if r["caught"] else "N/A   " if r["caught"] is None else "MISSED"
        print(f"{tag}  {r['mutation']:{width}s}  {r['first_failure'] or ''}")
    elapsed = round(time.time() - t0, 1)
    out = {"executed_at_utc": datetime.now(timezone.utc).isoformat(), "control_passed": True,
           "caught": caught, "total": len(results), "runtime_s": elapsed, "test_suite": TEST, "results": results}
    with open(os.path.join(RESULTS, "mutation_check.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(f"\n{caught}/{len(results)} planted verifier bugs caught by {TEST} in {elapsed}s")
    return 0 if caught == len(results) else 1
