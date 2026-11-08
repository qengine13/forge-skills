"""Run the real quality-gate CLI against disposable projects."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "skills/anti-slop-gates/scripts/slopgate.py"


class GateTests(unittest.TestCase):
    def run_gate(self, root, command):
        return subprocess.run(
            [sys.executable, str(ENGINE), command, "--root", str(root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_clean_then_new_inline_handler_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            page = root / "index.html"
            page.write_text("<main><button>Save</button></main>")
            initialized = self.run_gate(root, "init")
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            self.assertIn("architecture map review is not applicable", initialized.stdout)
            target = self.run_gate(root, "target")
            self.assertEqual(target.returncode, 0, target.stdout + target.stderr)
            self.assertIn("no target map is required", target.stdout)
            self.assertEqual(self.run_gate(root, "check").returncode, 0)
            page.write_text('<main><button onclick="save()">Save</button></main>')
            failed = self.run_gate(root, "check")
            self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
            self.assertIn("inline", failed.stdout.lower())
            self.assertEqual(self.run_gate(root, "update").returncode, 1)

    def test_lowered_baseline_blocks_return_of_old_debt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            page = root / "index.html"
            dirty = '<main><button onclick="save()">Save</button></main>'
            page.write_text(dirty)
            self.assertEqual(self.run_gate(root, "init").returncode, 0)
            page.write_text("<main><button>Save</button></main>")
            self.assertEqual(self.run_gate(root, "update").returncode, 0)
            page.write_text(dirty)
            self.assertEqual(self.run_gate(root, "check").returncode, 1)

    def test_native_gates_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".native-quality-gates").write_text("Use the existing project checks.")
            self.assertEqual(self.run_gate(root, "init").returncode, 2)
            self.assertFalse((root / ".slopgates").exists())
