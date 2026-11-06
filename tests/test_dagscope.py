"""Offline tests for the imported event graph and its integration with RootProof."""
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from rootproof.dagscope import data, routes, runner, verify
from rootproof.live import bus


class GraphTests(unittest.TestCase):
    def test_saved_teammate_results_reproduce_exactly(self):
        for name in data.results_sets():
            for expected in data.load_results(name)["per_task"]:
                with self.subTest(attempt_set=name, task=expected["task_id"]):
                    item = data.load_task(expected["task_id"])
                    artifact = data.load_artifact(item["task"]["scenario_id"])
                    actual = verify.grade(item["attempts"][name], item["task"], item["truth"], artifact)
                    self.assertEqual(actual, expected)

    def test_incomplete_trace_does_not_prove_missing_path(self):
        events = [{"event_id": "a"}, {"event_id": "b"}]
        graph = verify.build_dependency_dag(events, [], {"closed_world": False, "level": "partial"})
        claim = {"source_event_id": "a", "target_event_id": "b", "relationship": "reachable"}
        self.assertEqual(verify.verify_claim(claim, graph)["verdict"], "NOT_OBSERVED")
        graph.meta["coverage"] = {"closed_world": True, "level": "complete"}
        self.assertEqual(verify.verify_claim(claim, graph)["verdict"], "CONTRADICTED")

    def test_missing_parent_revokes_claimed_complete_coverage(self):
        events = [{"event_id": "a"}, {"event_id": "b"}]
        deps = [{"parent_event_id": "missing", "child_event_id": "b", "dependency_type": "service_execution"}]
        graph = verify.build_dependency_dag(events, deps, {"closed_world": True, "level": "complete"})
        claim = {"source_event_id": "a", "target_event_id": "b", "relationship": "reachable"}
        self.assertEqual(verify.verify_claim(claim, graph)["verdict"], "NOT_OBSERVED")

    def test_live_capture_timeout_does_not_claim_complete(self):
        graph = {"events": [], "dependencies": []}
        with patch.object(runner, "_graph", return_value=graph), patch.object(runner.time, "monotonic", side_effect=[0, 0, 2]), \
             patch.object(runner.time, "sleep"):
            with self.assertRaises(TimeoutError):
                runner.poll_until_quiescent("run", timeout_s=1)

    def test_capture_streams_before_producer_finishes(self):
        graph = {"events": [{"event_id": "a"}], "dependencies": []}
        emitted = []
        with patch.object(runner, "_graph", return_value=graph), patch.object(runner.time, "sleep"), \
             patch.object(runner.time, "monotonic", side_effect=[0, 0, 1, 2]), \
             patch.object(runner, "_edge_key"):
            runner.poll_until_quiescent("run", lambda kind, row: emitted.append(kind), quiet_polls=1,
                                        timeout_s=10, producer_done=iter([False, False, True]).__next__)
        self.assertEqual(emitted, ["dag_event"])

    def test_run_scenario_saves_capture_from_instrumented_graph(self):
        graph = {"events": [{"event_id": "a", "event_type": "request", "service": "gateway"}],
                 "dependencies": [], "integrity": {"valid": True}}
        events = []
        def poll(*args, producer_done):
            while not producer_done():
                pass
            return graph
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, "RUNS_DIR", Path(tmp)), \
             patch.object(runner, "healthy", return_value=True), \
             patch.dict(runner.SCENARIOS, {"scenario_1": lambda run_id: (run_id, [])}), \
             patch.object(runner, "poll_until_quiescent", side_effect=poll):
            result = runner.run_scenario("scenario_1", lambda kind, payload: events.append(kind))
            self.assertTrue(Path(result["saved_to"]).is_file())
        self.assertEqual(events, ["run_started", "run_saved"])

    def test_api_returns_saved_task_and_checks_claim(self):
        code, task = routes.handle_get("/api/dag/task/scenario_1_task_1")
        self.assertEqual(code, 200)
        pair = task["task"]["pairs"][0]
        request = {"run": "scenario_1", "claims": [{**pair, "relationship": "reachable"}]}
        code, result = routes.verify_request(request)
        self.assertEqual(code, 200)
        self.assertEqual(result["verdicts"][0]["verdict"], "SUPPORTED")
        self.assertGreater(len(result["verdicts"][0]["dependency_path"]), 1)

    def test_real_system_offline_falls_back_to_capture(self):
        with patch.object(runner, "healthy", return_value=False):
            code, result = routes.handle_post("/api/dag/run-live", {"scenario": "scenario_1"}, bus)
            job = bus.JOBS[result["job"]]
            events = list(job.stream(timeout=1))
        self.assertEqual(code, 200)
        self.assertIn("dag_captured", [e["type"] for e in events if e])
        self.assertNotIn("error", [e["type"] for e in events if e])


if __name__ == "__main__":
    unittest.main()
