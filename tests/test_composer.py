"""Validation and recovery for cases drafted by a model."""
import json
import tempfile
import unittest
from unittest.mock import patch

from rootproof.live import bus, cases, stream


def draft(value):
    return {"id": "dns-case", "title": "DNS failure", "faults": [{"id": "F1", "svc": "session-api", "field": "link",
            "value": value, "start": 120, "kind": "transient", "link": "meta-index", "mode": "dns"}],
            "events": [], "logs": {}, "truth": "DNS failure"}


class ComposerTests(unittest.TestCase):
    def test_ip_address_is_rejected_with_actionable_error(self):
        _, problems = stream._validate(draft("10.42.8.19"))
        self.assertTrue(any("numeric value" in p for p in problems))

    def test_zero_capacity_and_out_of_range_fraction_are_rejected(self):
        raw = draft(2)
        self.assertTrue(stream._validate(raw)[1])
        raw = draft(0)
        raw["faults"][0] = {"svc": "docstore", "field": "capacity", "value": 0, "start": 120}
        self.assertTrue(stream._validate(raw)[1])

    def test_invalid_first_draft_is_repaired_before_saving(self):
        responses = [(json.dumps(draft("10.42.8.19")), {}), (json.dumps(draft(0.9)), {})]
        job = bus.Job("draft")
        with tempfile.TemporaryDirectory() as directory, patch.object(cases, "CUSTOM_DIR", directory), \
             patch.object(stream, "ask_subject", side_effect=responses) as model, patch.object(stream, "truth_for", return_value=[]):
            stream.job_draft(job, "DNS issue")
            self.assertEqual(model.call_count, 2)
            self.assertIn("numeric value", model.call_args_list[1].args[0])
            self.assertEqual(len(cases.custom()), 1)
            self.assertEqual(cases.custom()[0].faults[0].value, 0.9)
        self.assertIn("case_ready", [e["type"] for e in job.events])

    def test_existing_custom_case_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cases, "CUSTOM_DIR", directory):
            a, _ = stream._validate(draft(0.8))
            b, _ = stream._validate(draft(0.9))
            first = cases.save(a)
            second = cases.save(b)
            self.assertNotEqual(first.id, second.id)
            self.assertEqual(cases.find(first.id).faults[0].value, 0.8)
            self.assertEqual(cases.find(second.id).faults[0].value, 0.9)


if __name__ == "__main__":
    unittest.main()
