"""Validate readable public instructions under a Windows-style text default."""

import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "package_validator", ROOT / "scripts/validate_package.py"
)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class PackageTests(unittest.TestCase):
    def test_public_package_reads_utf8_independently_of_locale(self):
        original_open = io.open

        def windows_default(file, mode="r", buffering=-1, encoding=None, *args, **kwargs):
            if "b" not in mode and encoding is None:
                encoding = "cp1252"
            return original_open(file, mode, buffering, encoding, *args, **kwargs)

        with patch("io.open", windows_default):
            self.assertEqual(validator.validate(), [])


class PublicContentTests(unittest.TestCase):
    def test_code_and_hidden_configuration_are_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".github").mkdir()
            private_path = "/" + "Users/fictional-person/private/"
            (root / "helper.py").write_text(repr(private_path), encoding="utf-8")
            (root / ".github/check.yml").write_text(private_path, encoding="utf-8")
            errors = validator.check_public_files(root)
            self.assertEqual(len(errors), 2)
            self.assertTrue(any("helper.py" in item for item in errors))
            self.assertTrue(any("check.yml" in item for item in errors))

    def test_memory_data_and_binary_artifacts_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "episodes.jsonl").write_text("{}", encoding="utf-8")
            (root / "image.png").write_bytes(bytes([255, 254, 253]))
            errors = validator.check_public_files(root)
            self.assertEqual(len(errors), 2)
            self.assertTrue(any("not distributable" in item for item in errors))
            self.assertTrue(any("metadata review" in item for item in errors))
