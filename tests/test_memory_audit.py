"""Exercise portable audit rules against fictional commits without reading user history."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/agent-memory/scripts"


class MemoryAuditTests(unittest.TestCase):
    def test_risk_paths_and_review_evidence(self):
        code = """
import json
from agent_memory_doctrine_audit import (
    GitCommit, detect_risk_review, _is_actual_codex_review_command,
)
commits = [
    GitCommit("a" * 40, "fix: authorization", ["src/auth/login.py"], 10),
    GitCommit("b" * 40, "fix: storage", ["src/memory/store.py"], 10),
    GitCommit("c" * 40, "docs: wording", ["README.md"], 10),
]
print(json.dumps({
    "unreviewed": [f.offending_ref for f in detect_risk_review(commits, set())],
    "reviewed": [f.offending_ref for f in detect_risk_review(commits, {"a" * 40})],
    "actual": _is_actual_codex_review_command("codex review --uncommitted"),
    "quoted": _is_actual_codex_review_command("echo 'codex review --uncommitted'"),
    "unrelated": _is_actual_codex_review_command("custom-review-helper"),
    "gate": detect_risk_review(commits, set())[0].gate_id,
}))
"""
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["FORGE_MEMORY_HOME"] = directory
            env["PYTHONPATH"] = str(SCRIPTS)
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=directory,
                env=env,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=20,
                check=True,
            )
            data = json.loads(result.stdout)
            self.assertEqual(data["unreviewed"], ["a" * 40, "b" * 40])
            self.assertEqual(data["reviewed"], ["b" * 40])
            self.assertTrue(data["actual"])
            self.assertFalse(data["quoted"])
            self.assertFalse(data["unrelated"])
            self.assertEqual(data["gate"], "risk_review")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_command_evidence_does_not_credit_quoted_or_structured_prose(self):
        code = r"""
import json
from agent_memory_doctrine_audit import (
    _is_actual_codex_review_command, _is_actual_plan_hygiene_command,
    _line_is_plan_hygiene_evidence, _line_is_plan_mode_evidence, _is_risk_path,
    _is_codex_review_result_line,
)
print(json.dumps({
    "quoted": [_is_actual_codex_review_command(c) for c in [
        'echo "noop; codex review"', "echo ';' codex review",
        'printf "codex review"', 'codex reviewer', 'echo "unterminated; codex review',
        'echo $(codex review)',
    ]],
    "steps": [_is_actual_codex_review_command(c) for c in [
        'cd /repo\ncodex review --uncommitted',
        'FOO=1 codex review', 'cd /repo && codex review',
        'FOO="a b" codex review', 'codex ' + chr(92) + chr(10) + 'review',

    ]],
    "prose": [_line_is_plan_hygiene_evidence(c) for c in [
        'echo "pre-flight verdict completed"',
        '{"summary":"pre-flight verdict completed"}',
        '["pre-flight verdict completed"]', 'FOO=1',
        '  "summary": "pre-flight verdict completed"',
        'FOO="pre-flight verdict completed"',

    ]],
    "result_lines": [_is_codex_review_result_line(c) for c in [
        'codex reviewer completed', 'codex review-helper completed', 'codex review completed',
    ]],
    "plan_mode": _line_is_plan_mode_evidence('echo "plan mode"'),
    "real_prose": _line_is_plan_hygiene_evidence('pre-flight verdict completed: PASS'),
    "skill": _is_actual_plan_hygiene_command('$plan-hygiene check the proposal'),
    "risk": [_is_risk_path(p) for p in [
        'src/auth.py', 'src/auth/login.py', 'src/memory/store.py',
        'docs/agent-memory/guide.md', 'src/live_preview.js', 'tests/test_memory.py',
    ]],
}))
"""
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["FORGE_MEMORY_HOME"] = directory
            env["PYTHONPATH"] = str(SCRIPTS)
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=directory,
                env=env,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=20,
                check=True,
            )
            data = json.loads(result.stdout)
            self.assertEqual(data["quoted"], [False] * 6)
            self.assertEqual(data["steps"], [True] * 5)
            self.assertEqual(data["prose"], [False] * 6)
            self.assertEqual(data["result_lines"], [False, False, True])
            self.assertFalse(data["plan_mode"])
            self.assertTrue(data["real_prose"])
            self.assertTrue(data["skill"])
            self.assertEqual(data["risk"], [True] * 3 + [False] * 3)
            self.assertEqual(list(Path(directory).iterdir()), [])
