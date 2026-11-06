"""Harness behaviour: parsing, first-attempt protection, scoring denominators, no credentials needed."""
import json

import pytest

from app.evaluation import harness
from app.evaluation.model_client import Completion, ModelConfig
from app.evaluation.schema import parse_answer
from app.evaluation.scoring import aggregate

GOOD = '{"claims":[{"source_event_id":"a","target_event_id":"b","relationship":"reachable"}],"explanation":"x","confidence":0.5}'


@pytest.mark.parametrize("raw,ok", [
    (GOOD, True),
    (f"```json\n{GOOD}\n```", True),
    (f"```\n{GOOD}\n```", True),
    ("The answer is reachable.", False),
    (f"Sure! Here it is: {GOOD}", False),
    ("", False),
    (None, False),
    ('{"claims": [], "explanation": "x"}', False),
    ('{"claims": [], "explanation": "x", "confidence": 7}', False),
    ('{"claims": [{"source_event_id": "a"}], "explanation": "x", "confidence": 0.5}', False),
])
def test_parsing(raw, ok):
    parsed, error = parse_answer(raw)
    assert (parsed is not None) == ok
    assert (error is None) == ok


def test_unknown_relationship_parses_so_the_checker_can_reject_it():
    parsed, _ = parse_answer('{"claims":[{"source_event_id":"a","target_event_id":"b","relationship":"caused"}],"explanation":"x","confidence":0.5}')
    assert parsed.claims[0].relationship == "caused"


TASK = {"task_id": "scenario_1_task_1", "scenario_id": "scenario_1", "question": "q", "pairs": [], "coverage": "complete",
        "ai_visible_logs": "log", "ai_visible_logs_no_parents": "log"}


def test_first_attempt_is_stored_verbatim_and_never_regenerated(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "BENCH", tmp_path)
    calls = []

    def fake(config, prompt, max_tokens=16000):
        calls.append(prompt)
        return Completion("not json at all", "stop", {"total_tokens": 5}, "m-1")

    monkeypatch.setattr(harness, "complete_once", fake)
    config = ModelConfig(api_key="k", model="m/1")
    first = harness.run_attempt(config, TASK, "explicit_parents")
    second = harness.run_attempt(config, TASK, "explicit_parents")
    assert len(calls) == 1
    assert first["raw_response"] == second["raw_response"] == "not json at all"
    assert first["parsed"] is None and "invalid JSON" in first["parse_error"]
    assert first["token_usage"] == {"total_tokens": 5}
    assert first["model_name"] == "m/1" and first["evaluated_at_utc"]
    assert first["source"] == "model"
    saved = json.loads((tmp_path / "model_attempts" / "m_1__explicit_parents" / "scenario_1_task_1.json").read_text())
    assert saved["prompt"] == calls[0]


def test_transport_errors_are_retried_and_logged_but_answers_are_not(monkeypatch):
    from unittest.mock import MagicMock

    import httpx
    from openai import APIConnectionError

    from app.evaluation import model_client

    answer = MagicMock()
    answer.choices = [MagicMock(finish_reason="stop", message=MagicMock(content="wrong answer"))]
    answer.usage = None
    answer.model = "m"
    client = MagicMock()
    client.chat.completions.create.side_effect = [APIConnectionError(request=httpx.Request("POST", "http://x")), answer]
    monkeypatch.setattr(model_client, "OpenAI", lambda **_: client)
    monkeypatch.setattr(model_client.time, "sleep", lambda _: None)
    done = model_client.complete_once(ModelConfig(api_key="k", model="m"), "p")
    assert done.raw_text == "wrong answer"
    assert client.chat.completions.create.call_count == 2
    assert done.transport_errors == ["attempt 1: APIConnectionError"]
    sent = client.chat.completions.create.call_args.kwargs["messages"]
    assert sent == [{"role": "user", "content": "p"}]


def test_missing_credentials_yield_no_config(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    assert ModelConfig.from_env() is None


def _graded(coverage, claims, correct=False, fmt=False):
    return {"coverage": coverage, "category": "x", "task_correct": correct, "format_failure": fmt, "claims": claims}


def _claim(rel, verdict, in_full):
    return {"raw": {"relationship": rel}, "result": {"verdict": verdict}, "reachable_in_private_graph": in_full}


def test_metric_definitions_and_denominators():
    metrics = aggregate([
        _graded("complete", [_claim("reachable", "CONTRADICTED", False), _claim("reachable", "SUPPORTED", True),
                             _claim("not_reachable", "CONTRADICTED", True), _claim("not_reachable", "SUPPORTED", False)]),
        _graded("partial", [_claim("reachable", "NOT_OBSERVED", True), _claim("not_reachable", "NOT_OBSERVED", False),
                            _claim("insufficient_evidence", "SUPPORTED", True), _claim("reachable", "SUPPORTED", True)]),
        _graded("complete", [], fmt=True),
    ])
    assert metrics["false_positive_reachability"]["count"] == 1 and metrics["false_positive_reachability"]["denominator"] == 4
    assert metrics["false_negative_reachability_complete"]["count"] == 1 and metrics["false_negative_reachability_complete"]["denominator"] == 2
    assert metrics["incorrect_certainty_incomplete"]["count"] == 2 and metrics["incorrect_certainty_incomplete"]["denominator"] == 3
    assert metrics["claims_accepted"] == 4 and metrics["claims_rejected"] == 2 and metrics["claims_inconclusive"] == 2
    assert metrics["format_failures"] == 1 and metrics["tasks_evaluated"] == 3
