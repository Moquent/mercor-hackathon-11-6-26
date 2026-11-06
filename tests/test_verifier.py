"""Fast offline tests of the verifier itself (no network: Jev answers come from results/jev_cache.json).

    python3 -m unittest tests/test_verifier.py -v

Expected values are either hand labels (rootproof/labels.py, the selftest traces) or the stored outputs of the
last full evaluation (results/summary.json), so a change in verifier behaviour shows up as a failure here.
"""
import json
import os
import sys
import unittest
from unittest.mock import patch
from concurrent.futures import ProcessPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from rootproof.agent.run import AGENT_INCIDENTS, load  # noqa: E402
from rootproof.harness import load_attempt  # noqa: E402
from rootproof.labels import REFERENCE, TRAP  # noqa: E402
from rootproof.scenario.actions import catalog_for  # noqa: E402
from rootproof.scenario.incidents import INCIDENTS  # noqa: E402

BY_ID = {i.id: i for i in list(INCIDENTS) + list(AGENT_INCIDENTS)}
WORKERS = min(8, os.cpu_count() or 4)


def _read(path):
    with open(path) as f:
        return json.load(f)


SUMMARY = _read(os.path.join(REPO, "results", "summary.json"))


# ---- workers (module level so ProcessPoolExecutor can pickle them) ----

def _labels_job(task_id):
    from rootproof.verify.verdict import verify
    inc = BY_ID[task_id]
    ref, trap = verify(inc, REFERENCE[task_id]), verify(inc, TRAP[task_id])
    return task_id, ref.to_dict(), trap.to_dict()


def _ai_job(key):
    from rootproof.verify.verdict import verify
    task_id, run_no = key
    return key, verify(BY_ID[task_id], load_attempt(task_id, run_no)["answer"]).to_dict()


def _agent_job(key):
    from rootproof.verify.trace import verify_trace
    from rootproof.verify.verdict import verify
    task_id, run_no = key
    inc, rec = BY_ID[task_id], load(task_id, run_no)
    return key, verify(inc, rec.get("answer") or "", catalog_for(task_id)).to_dict(), verify_trace(inc, rec)


def _truth_job(task_id):
    from rootproof.verify.replay import ground_truth
    inc = BY_ID[task_id]
    sets = ground_truth(inc, catalog_for(task_id)) if task_id in AGENT_IDS else ground_truth(inc)
    return task_id, [list(x) for x in sets]


def _seed_job(args):
    from rootproof.verify.replay import resolves_on_all_seeds
    task_id, actions = args
    return task_id, resolves_on_all_seeds(BY_ID[task_id], actions)


def _chain_job(task_id):
    from rootproof.verify.chain import true_graph
    edges, origins = true_graph(BY_ID[task_id])
    return task_id, sorted(list(e) for e in edges), origins


AGENT_IDS = {"16-link-partition", "17-origin-partition"}
TRUTH_IDS = ["01-vela-402", "03-cache-oom-blip", "05-relay-storm", "08-docstore-primary", "12-cache-ttl"]
AI_KEYS = [(v["task"], v["run"]) for v in SUMMARY["verdicts"] if v["kind"] == "ai"]
AGENT_KEYS = [(r["task"], r["run"]) for r in SUMMARY["investigation"]["runs"]]
R = {}


def setUpModule():
    """Every slow computation runs once, in parallel processes."""
    seeds_input = [(v["task"], list(v["extraction"]["actions"])) for v in SUMMARY["verdicts"]
                   if v["kind"] == "ai" and v["run"] == 1 and v["fixed_by_seed"]][:6]
    with ProcessPoolExecutor(WORKERS) as ex:
        futs = {
            "labels": ex.map(_labels_job, [i.id for i in INCIDENTS]),
            "ai": ex.map(_ai_job, AI_KEYS),
            "agent": ex.map(_agent_job, AGENT_KEYS),
            "truth": ex.map(_truth_job, TRUTH_IDS + sorted(AGENT_IDS)),
            "seeds": ex.map(_seed_job, seeds_input),
            "chain": ex.map(_chain_job, [i.id for i in INCIDENTS]),
        }
        R["labels"] = {t: (ref, trap) for t, ref, trap in futs["labels"]}
        R["ai"] = dict(futs["ai"])
        R["agent"] = {k: (v, tr) for k, v, tr in futs["agent"]}
        R["truth"] = dict(futs["truth"])
        R["seeds"] = list(zip(seeds_input, [ok for _, ok in futs["seeds"]]))
        R["chain"] = {t: (e, o) for t, e, o in futs["chain"]}


def _checks(v):
    return [(c["check"], c["pass"]) for c in v["invariants"]]


class TestLabels(unittest.TestCase):
    def test_reference_accepted_trap_not(self):
        self.assertEqual(len(R["labels"]), 15)
        for task, (ref, trap) in R["labels"].items():
            with self.subTest(task=task):
                self.assertEqual(ref["verdict"], "ACCEPT", ref["certificates"])
                self.assertNotEqual(trap["verdict"], "ACCEPT")

    def test_reference_fix_holds_on_every_seed(self):
        for task, (ref, _) in R["labels"].items():
            if not BY_ID[task].no_outage:
                with self.subTest(task=task):
                    self.assertEqual(ref["fixed_by_seed"], [True] * 5)
                    self.assertTrue(ref["fixed_all_seeds"])

    def test_trap_failure_reasons(self):
        """Each trap is rejected for the reason the incident is built around."""
        failed = {t: {c["check"] for c in trap["invariants"] if not c["pass"]} for t, (_, trap) in R["labels"].items()}
        self.assertIn("origin", failed["01-vela-402"])
        self.assertIn("reachability", failed["05-relay-storm"])
        self.assertIn("origin", failed["06-meta-creds"])
        self.assertIn("origin", failed["08-docstore-primary"])
        self.assertIn("temporal", failed["14-double-fault"])
        self.assertIn("temporal", failed["15-loud-masks-quiet"])
        self.assertEqual(failed["11-phantom-drop"], {"impact"})
        # half-fixes: story right, actions insufficient -> PARTIAL with a replay certificate
        for t in ("02-cache-cpu-limit", "09-cache-node-loss", "12-cache-ttl", "13-cache-pool"):
            trap = R["labels"][t][1]
            self.assertEqual((trap["verdict"], trap["fixed_in_replay"]), ("PARTIAL", False), t)
            self.assertTrue(any("Replay with the AI's actions" in c for c in trap["certificates"]), t)

    def test_temporal_certificate_for_late_rollback(self):
        trap = R["labels"]["06-meta-creds"][1]
        self.assertEqual(trap["fixed_in_replay"], False)
        self.assertTrue(any(c.startswith("Temporal:") for c in trap["certificates"]), trap["certificates"])


class TestStoredVerdicts(unittest.TestCase):
    def test_every_saved_answer_matches_summary(self):
        stored = {(v["task"], v["run"]): v for v in SUMMARY["verdicts"] if v["kind"] == "ai"}
        self.assertEqual(len(R["ai"]), 75)
        for key, got in R["ai"].items():
            want = stored[key]
            with self.subTest(run=key):
                self.assertEqual(got["verdict"], want["verdict"])
                self.assertEqual(got["fixed_in_replay"], want["fixed_in_replay"])
                self.assertEqual(got["fixed_by_seed"], want["fixed_by_seed"])
                self.assertEqual(sorted(got["extraction"]["actions"]), sorted(want["extraction"]["actions"]))
                self.assertEqual(got["extraction"]["roots"], want["extraction"]["roots"])
                self.assertEqual(_checks(got), _checks(want))
                self.assertAlmostEqual(got["ai_error_rate_after"], want["ai_error_rate_after"], places=3)

    def test_seed_sweep_consistent(self):
        self.assertTrue(R["seeds"])
        stored = {(v["task"], v["run"]): v for v in SUMMARY["verdicts"] if v["kind"] == "ai"}
        for (task, _), ok in R["seeds"]:
            with self.subTest(task=task):
                self.assertEqual(ok, all(stored[(task, 1)]["fixed_by_seed"]))

    def test_seed_sweep_catches_fragile_fixes(self):
        """Some saved runs pass on seed 0 only partially across seeds; the sweep must report it, not copy seed 0."""
        mixed = [v for v in SUMMARY["verdicts"] if v["fixed_by_seed"] and len(set(v["fixed_by_seed"])) > 1]
        for v in mixed:
            if v["kind"] == "ai":
                self.assertEqual(R["ai"][(v["task"], v["run"])]["fixed_by_seed"], v["fixed_by_seed"])


class TestInvestigation(unittest.TestCase):
    def test_selftest(self):
        from rootproof.investigation import selftest
        out = selftest()
        self.assertEqual(len(out), 4)
        for r in out:
            self.assertTrue(r["pass"], r)

    def test_link_partition_run1(self):
        tr = R["agent"][("16-link-partition", 1)][1]
        self.assertEqual(tr["counts"], {"VERIFIED": 7, "UNSUPPORTED": 0, "UNGROUNDED": 0, "NOT_OBSERVED": 0, "FALSE": 1, "MALFORMED": 0})
        self.assertFalse(tr["clean"])
        fw = tr["first_wrong"]
        self.assertEqual((fw["step"], fw["label"], fw["claim"]), ("s15", "FALSE", "player-core -> media-cache (failure)"))
        self.assertEqual(fw["would_have_caught_it"]["tool"], "check_connectivity")
        self.assertEqual(fw["would_have_caught_it"]["args"], {"src": "player-core", "dst": "media-cache"})
        self.assertEqual([(m["tool"], m["args"]) for m in tr["missed_checks"]],
                         [("check_connectivity", {"src": "player-core", "dst": "media-cache"})])

    def test_link_partition_run2_clean(self):
        tr = R["agent"][("16-link-partition", 2)][1]
        self.assertTrue(tr["clean"])
        self.assertEqual(tr["counts"]["VERIFIED"], 8)
        self.assertIsNone(tr["first_wrong"])
        self.assertEqual(tr["missed_checks"], [])

    def test_origin_partition_runs(self):
        tr1 = R["agent"][("17-origin-partition", 1)][1]
        self.assertEqual(tr1["counts"], {"VERIFIED": 12, "UNSUPPORTED": 1, "UNGROUNDED": 0, "NOT_OBSERVED": 0, "FALSE": 1, "MALFORMED": 0})
        self.assertEqual((tr1["first_wrong"]["step"], tr1["first_wrong"]["label"]), ("s3", "UNSUPPORTED"))
        tr2 = R["agent"][("17-origin-partition", 2)][1]
        self.assertTrue(tr2["clean"])
        self.assertEqual([(m["tool"], m["args"]) for m in tr2["missed_checks"]],
                         [("check_connectivity", {"src": "media-cache", "dst": "vela-origin"})])

    def test_every_trace_matches_summary(self):
        stored = {(r["task"], r["run"]): r for r in SUMMARY["investigation"]["runs"]}
        for key, (verdict, tr) in R["agent"].items():
            want = stored[key]
            with self.subTest(run=key):
                self.assertEqual([c["label"] for c in tr["claims"]], [c["label"] for c in want["trace"]["claims"]])
                self.assertEqual(tr["clean"], want["trace"]["clean"])
                self.assertEqual([(m["tool"], m["args"]) for m in tr["missed_checks"]],
                                 [(m["tool"], m["args"]) for m in want["trace"]["missed_checks"]])
                fw, wfw = tr["first_wrong"], want["trace"]["first_wrong"]
                self.assertEqual(fw and (fw["step"], fw["label"]), wfw and (wfw["step"], wfw["label"]))
                self.assertEqual(verdict["verdict"], want["verdict"]["verdict"])
                self.assertEqual(verdict["fixed_by_seed"], want["verdict"]["fixed_by_seed"])
                self.assertEqual(_checks(verdict), _checks(want["verdict"]))


class TestGroundTruth(unittest.TestCase):
    def test_ground_truth_matches_summary(self):
        for t in TRUTH_IDS:
            with self.subTest(task=t):
                self.assertEqual(R["truth"][t], SUMMARY["ground_truth"][t])
        for t in AGENT_IDS:
            with self.subTest(task=t):
                self.assertEqual(R["truth"][t], SUMMARY["investigation"]["ground_truth"][t])

    def test_known_minimal_fixes(self):
        self.assertEqual(R["truth"]["01-vela-402"], [["restore_vela"]])
        self.assertIn(["player_retries_off"], R["truth"]["03-cache-oom-blip"])
        self.assertEqual(R["truth"]["16-link-partition"], [["rollback_netpolicy"]])


class TestCausalChain(unittest.TestCase):
    def test_cache_ttl_origin_edge(self):
        edges, origins = R["chain"]["12-cache-ttl"]
        self.assertIn(["media-cache", "vela-origin"], edges)
        self.assertEqual(origins, ["media-cache"])

    def test_true_graphs_match_summary(self):
        for t, (edges, origins) in R["chain"].items():
            want = SUMMARY["causal_chains"][t]
            with self.subTest(task=t):
                self.assertEqual(edges, sorted(want["true_edges"]))
                self.assertEqual(origins, want["origins"])

    def test_vela_failure_edge_direction(self):
        edges, _ = R["chain"]["01-vela-402"]
        self.assertIn(["vela-origin", "media-cache"], edges)
        self.assertIn(["media-cache", "player-core"], edges)
        self.assertNotIn(["player-core", "media-cache"], edges)


class TestRCAEval(unittest.TestCase):
    def test_checks_reproduce_stored_rows(self):
        from rootproof.config import RCAEVAL_ATTEMPTS, RCAEVAL_TASKS
        from rootproof.rcaeval.checks import check
        from rootproof.rcaeval.score import parse
        rows = SUMMARY["rcaeval"]["rows"]
        self.assertGreaterEqual(len(rows), 10)
        for row in rows:
            case = row["case"]
            with self.subTest(case=case):
                features = _read(os.path.join(RCAEVAL_TASKS, case, "features.json"))
                claim = parse(_read(os.path.join(RCAEVAL_ATTEMPTS, f"{case}.json"))["answer"])
                checks, svc, _ = check(features, claim)
                self.assertEqual(svc, row["ai_service"])
                self.assertEqual([c.to_dict() for c in checks], row["checks"])
                passed = all(c.passed is not False for c in checks)
                self.assertEqual("PASS" if passed else "FLAG", row["verifier"])

    def test_some_rows_flagged_by_each_check(self):
        flagged = {c["check"] for r in SUMMARY["rcaeval"]["rows"] for c in r["checks"] if c["pass"] is False}
        self.assertIn("signature", flagged)


class TestEvidenceBoundaries(unittest.TestCase):
    def test_seed_sweep_reports_each_seed(self):
        from rootproof.verify.verdict import verify
        inc = BY_ID["01-vela-402"]
        with patch("rootproof.verify.verdict.resolves", side_effect=lambda incident, actions, seed: seed != 3) as mocked:
            verdict = verify(inc, REFERENCE[inc.id])
        self.assertEqual(verdict.fixed_by_seed, [True, True, True, False, True])
        self.assertFalse(verdict.fixed_all_seeds)
        self.assertEqual([call.args[2] for call in mocked.call_args_list], [0, 1, 2, 3, 4])

    def test_robust_replay_requires_all_seeds(self):
        from rootproof.verify.replay import resolves_on_all_seeds
        with patch("rootproof.verify.replay.resolves", side_effect=lambda incident, actions, seed: seed != 3):
            self.assertFalse(resolves_on_all_seeds(BY_ID["01-vela-402"], ["restore_vela"]))

    def test_preexisting_noise_is_not_an_incident_origin(self):
        from rootproof.scenario.incidents import topology_for
        from rootproof.scenario.topology import ENTRY
        from rootproof.sim import run
        from rootproof.verify.claims import _root_checks, onset
        inc = BY_ID["05-relay-storm"]
        rows = run(topology_for(inc), inc.faults, ENTRY)
        baseline = next(c for c in _root_checks(inc, rows, "clipstream-origin", onset(rows, ENTRY), None)
                        if c.name == "baseline")
        self.assertFalse(baseline.passed)

    def test_rcaeval_resource_signature_requires_matching_metric(self):
        from rootproof.rcaeval.checks import _signature_check
        profile = {"checkoutservice": {"z": {"cpu": 1.0}}}
        self.assertFalse(_signature_check("checkoutservice", "cpu", profile).passed)
        profile["checkoutservice"]["z"]["cpu"] = 25.0
        self.assertTrue(_signature_check("checkoutservice", "cpu", profile).passed)

    def test_uninspectable_job_does_not_count_as_unverified_service(self):
        from rootproof.investigation import _record
        from rootproof.verify.trace import verify_trace
        inc = BY_ID["04-docstore-poller"]
        claim = {"kind": "cause", "service": "sub-poller", "evidence": ["s1"]}
        without = _record(inc, [("get_metrics", {"service": "docstore"})], [claim], "sub-poller", [])
        with_change = _record(inc, [("get_changes", {})], [claim], "sub-poller", [])
        self.assertEqual(verify_trace(inc, without)["claims"][0]["label"], "NOT_OBSERVED")
        self.assertEqual(verify_trace(inc, with_change)["claims"][0]["label"], "VERIFIED")

    def test_unknown_root_does_not_mean_no_outage(self):
        from rootproof.investigation import _record
        from rootproof.verify.trace import verify_trace
        inc = BY_ID["11-phantom-drop"]
        claim = {"kind": "cause", "service": "invented-service", "evidence": ["s1"]}
        record = _record(inc, [("get_changes", {})], [claim], "invented-service", [])
        self.assertEqual(verify_trace(inc, record)["claims"][0]["label"], "FALSE")


if __name__ == "__main__":
    unittest.main()
