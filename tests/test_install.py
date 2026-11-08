"""Exercise installation against isolated projects."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallationTests(unittest.TestCase):
    def test_both_agents_receive_complete_packages(self):
        for agent, folder in installer.DESTINATIONS.items():
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as temp:
                project = Path(temp)
                names = [p.name for p in installer.SOURCE.iterdir()]
                paths = installer.install(project, agent, names)
                self.assertEqual(len(paths), len(names))
                for source in installer.SOURCE.rglob("*"):
                    if (
                        source.is_file()
                        and "__pycache__" not in source.parts
                        and source.suffix != ".pyc"
                    ):
                        target = project / folder / source.relative_to(installer.SOURCE)
                        self.assertEqual(source.read_bytes(), target.read_bytes())

    def test_collision_aborts_every_copy_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            original = project / ".claude/skills/plan-hygiene"
            original.mkdir(parents=True)
            (original / "SKILL.md").write_text("user custom version")
            with self.assertRaises(ValueError):
                installer.install(project, "claude", ["completion-hygiene", "plan-hygiene"])
            self.assertFalse((original.parent / "completion-hygiene").exists())
            self.assertEqual((original / "SKILL.md").read_text(), "user custom version")

    def test_project_forge_includes_its_sibling_dependency(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = installer.install(Path(temp), "codex", ["project-forge"])
            self.assertEqual({p.name for p in paths}, {"project-forge", "anti-slop-gates"})

    def test_dry_run_does_not_create_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            installer.install(Path(temp), "claude", ["plan-hygiene"], True)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_symlinked_target_is_refused(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            tempfile.TemporaryDirectory() as outside,
        ):
            project = Path(temp)
            (project / ".claude").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                installer.install(project, "claude", ["plan-hygiene"])
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_missing_project_is_not_created(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "typo"
            with self.assertRaises(ValueError):
                installer.install(missing, "claude", ["plan-hygiene"])
            self.assertFalse(missing.exists())
