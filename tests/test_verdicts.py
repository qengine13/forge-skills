"""Exercise meaningful approval, malformed-input, and dissent boundaries."""

import copy
import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/crucible-forge/scripts"
sys.path.insert(0, str(SCRIPTS))
aggregate = importlib.import_module("aggregate_verdicts").aggregate
validate_payload = importlib.import_module("validate_verdict").validate_payload


def panel(name="reviewer-a"):
    value = json.loads((ROOT / "examples/reviewer-a.json").read_text())
    value["panelist"] = name
    return value


class VerdictTests(unittest.TestCase):
    def test_consistent_evidence_supported_panels_can_pass(self):
        result = aggregate([panel(), panel("b")])
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(validate_payload(result), [])

    def test_one_missing_evidence_vote_blocks_three_passes(self):
        missing = panel("d")
        missing.update(
            verdict="INSUFFICIENT_EVIDENCE",
            decision="defer",
            confidence=0,
            evidence_demanded=["Rollback evidence"],
            outstanding_objections=["Unknown failure recovery"],
        )
        result = aggregate([panel("a"), panel("b"), panel("c"), missing])
        self.assertEqual(result["verdict"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("Unknown failure recovery", result["outstanding_objections"])
        self.assertEqual(validate_payload(result), [])

    def test_failure_is_preserved(self):
        failure = panel("b")
        failure.update(
            verdict="FAIL",
            decision="reject",
            outstanding_objections=["Data loss reproduced"],
        )
        self.assertEqual(aggregate([panel(), failure])["verdict"], "INSUFFICIENT_EVIDENCE")
        other = copy.deepcopy(failure)
        other["panelist"] = "c"
        self.assertEqual(aggregate([failure, other])["verdict"], "FAIL")

    def test_unresolved_pass_and_malformed_fields_are_rejected(self):
        cases = [
            [],
            None,
            {"confidence": True},
            {"confidence": float("nan")},
            {"confidence": float("inf")},
            {"outstanding_objections": ["Unresolved"]},
            {"evidence_demanded": ["Missing"]},
            {"evidence_provided": []},
            {"kill_conditions": []},
            {"decision": "reject"},
            {"verdict": []},
            {"next_3_steps": [None]},
            {"panelist": " "},
            {"confidence": -1},
        ]
        for change in cases:
            with self.subTest(change=change):
                value = panel()
                if isinstance(change, dict):
                    value.update(change)
                else:
                    value = change
                self.assertTrue(validate_payload(value))

    def test_duplicate_panelists_do_not_count_as_independent(self):
        with self.assertRaises(ValueError):
            aggregate([panel(), copy.deepcopy(panel())])

    def test_empty_or_single_panel_does_not_pass(self):
        for panels in [[], [panel()]]:
            with self.assertRaises(ValueError):
                aggregate(panels)

    def test_cli_nonapproval_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = panel("b")
            missing.update(verdict="INSUFFICIENT_EVIDENCE", decision="defer")
            file = Path(temp) / "b.json"
            file.write_text(json.dumps(missing))
            output = Path(temp) / "result.json"
            command = [
                sys.executable,
                str(SCRIPTS / "aggregate_verdicts.py"),
                str(ROOT / "examples/reviewer-a.json"),
                str(file),
                "--out",
                str(output),
            ]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1, result.stderr)
            previous = output.read_bytes()
            self.assertEqual(
                subprocess.run(command, capture_output=True, check=False).returncode, 2
            )
            self.assertEqual(output.read_bytes(), previous)

    def test_validator_cli_handles_bad_file_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "bad.json"
            for text in ["[]", "{", "null"]:
                file.write_text(text)
                result = subprocess.run(
                    [sys.executable, str(SCRIPTS / "validate_verdict.py"), str(file)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr)
