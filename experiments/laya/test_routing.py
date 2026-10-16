"""Offline checks for benchmark accounting; no model download or credentials."""
import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest

import routing


class RoutingTests(unittest.TestCase):
    def test_report_records_resolved_precision_and_rejects_unpinned_weights(self):
        agent = SimpleNamespace(revision="a" * 40, device="cpu", amp_enabled=True, dtype="torch.bfloat16")
        self.assertEqual(routing.model_metadata(agent, "a" * 40), {
            "revision": "a" * 40, "device": "cpu", "amp_enabled": True, "dtype": "torch.bfloat16"})
        for revision in (None, "b" * 40):
            agent.revision = revision
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                routing.model_metadata(agent, "a" * 40)

    def test_fixture_labels_never_enter_model_state(self):
        rows = [{"id": "one", "state": "Cause unknown.", "expected": "investigate"}]
        self.assertEqual(routing.model_states(rows), ["Cause unknown."])

    def test_fixture_rejects_duplicate_ids_and_unknown_labels(self):
        good = {"id": "one", "state": "Cause unknown.", "expected": "investigate"}
        for rows in ([good, good], [{**good, "expected": "merge"}],
                     [{**good, "state": ""}], []):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                routing.validate_cases(rows)

    def test_accounting_keeps_abstentions_and_errors_in_denominator(self):
        rows = [
            {"expected": "deliver", "predicted": "deliver", "confidence": .8, "input_tokens": 40, "seconds": 1.},
            {"expected": "investigate", "predicted": "deliver", "confidence": .9, "input_tokens": 60, "seconds": 2.},
            {"expected": "stop", "predicted": None, "confidence": None, "input_tokens": 0, "seconds": 3.},
        ]
        report = routing.summarize(rows, .85)
        self.assertEqual(report["cases"], 3)
        self.assertEqual(report["correct"], 1)
        self.assertEqual(report["accepted"], 1)
        self.assertEqual(report["accepted_correct"], 0)
        self.assertEqual(report["premature_deliver"], 1)
        self.assertEqual(report["input_tokens"], 100)
        self.assertAlmostEqual(report["coverage"], 1 / 3)

    def test_malformed_probabilities_cannot_be_scored_as_a_success(self):
        good = {"answers": {"route": {"choice": "deliver", "probabilities":
            {"deliver": .7, "investigate": .1, "plan": .1, "stop": .1}}},
            "usage": {"input_tokens": 100, "output_tokens": 0}}
        self.assertEqual(routing.read_answer(good), ("deliver", .7, 100))
        for value in (float("nan"), -1, 2):
            bad = copy.deepcopy(good)
            bad["answers"]["route"]["probabilities"]["deliver"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                routing.read_answer(bad)
        bad = copy.deepcopy(good)
        bad["answers"]["route"]["choice"] = "merge"
        with self.assertRaises(ValueError):
            routing.read_answer(bad)

    def test_token_audit_detects_lost_state_instructions_and_options(self):
        class Tokens:
            cls_token_id, sep_token_id, mask_token_id = 1, 2, 3
            mask_token = "[MASK]"
            def encode(self, text, **kwargs):
                return [ord(c) for c in text]
        tok = Tokens()
        question = {"type": "choice", "instructions": "Pick.", "criteria": {"x": "Do."}}
        full = [1] + list(map(ord, "choice question: Pick.")) + [2, 3]
        full += list(map(ord, " x: Do.")) + [2] + list(map(ord, "State")) + [2]
        routing.check_sequence(tok, "State", question, full)
        for at in (2, 28, len(full) - 2):
            with self.subTest(at=at), self.assertRaises(ValueError):
                routing.check_sequence(tok, "State", question, full[:at] + full[at + 1:])

    def test_validate_command_needs_no_optional_dependencies(self):
        result = subprocess.run([sys.executable, str(Path(routing.__file__)), "--validate"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(json.loads(result.stdout)["cases"], 0)


if __name__ == "__main__":
    unittest.main()
