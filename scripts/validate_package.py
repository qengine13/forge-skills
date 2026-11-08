#!/usr/bin/env python3
"""Check distributable skill metadata, links, portability, and shared policy."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import yaml

ROOT = Path(__file__).resolve().parents[1]


IGNORED_DIRECTORIES = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}


def check_public_files(root: Path) -> list[str]:
    """Check every distributable file, including code and hidden configuration.

    This is a regression guard, not a substitute for semantic privacy review.
    """
    errors = []
    patterns = {
        "personal absolute path": r"/(?:Users|home)/[^/\s)]+/|[A-Z]:\\Users\\[^\\\s]+\\",
        "private key header": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
        "credential-bearing URL": r"[a-z]+://[^\s/:]+:[^\s/@]+@",
        "provider token candidate": r"(?:gh[pousr]_[A-Za-z0-9]{30,}|(?:AKIA|ASIA)[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{32,})",
    }
    for file in root.rglob("*"):
        relative = file.relative_to(root)
        if any(part in IGNORED_DIRECTORIES for part in relative.parts):
            continue
        if file.is_symlink():
            errors.append(f"{relative}: symlink requires explicit distribution review")
            continue
        if not file.is_file():
            continue
        if file.name in {".env", "episodes.jsonl", ".mcp.json", ".DS_Store"} or file.suffix in {
            ".sqlite",
            ".sqlite3",
            ".db",
            ".pem",
            ".key",
            ".pyc",
        }:
            errors.append(f"{relative}: private or generated artifact is not distributable")
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"{relative}: binary asset requires explicit metadata review")
            continue
        for category, pattern in patterns.items():
            if re.search(pattern, text):
                errors.append(f"{relative}: {category}")
    return errors


def validate() -> list[str]:
    """Return package defects without modifying files.

    Example:
        assert validate() == []
    """
    errors = check_public_files(ROOT)
    policy = (ROOT / "docs/model-policy.md").read_text(encoding="utf-8")
    for folder in sorted((ROOT / "skills").iterdir()):
        entry = folder / "SKILL.md"
        text = entry.read_text(encoding="utf-8")
        try:
            metadata = yaml.safe_load(text.split("---", 2)[1])
            if metadata["name"] != folder.name or not isinstance(metadata["description"], str):
                errors.append(f"{entry.relative_to(ROOT)}: invalid name or description")
            if not set(metadata) <= {"name", "description", "metadata", "license"}:
                errors.append(f"{entry.relative_to(ROOT)}: unsupported metadata")
        except (ValueError, IndexError, KeyError, TypeError, yaml.YAMLError) as exc:
            errors.append(f"{entry.relative_to(ROOT)}: invalid YAML: {exc}")
        if (folder / "references/model-policy.md").read_text(encoding="utf-8") != policy:
            errors.append(f"{folder.name}: shared model policy differs")
        for file in folder.rglob("*.py"):
            try:
                ast.parse(file.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                errors.append(f"{file.relative_to(ROOT)}: {exc}")
    for file in ROOT.rglob("*.md"):
        if any(part.startswith(".") for part in file.relative_to(ROOT).parts):
            continue
        text = file.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if target.startswith(("https://", "http://", "#", "mailto:")):
                continue
            path = unquote(target.split("#", 1)[0].strip("<>"))
            if path and not (file.parent / path).exists():
                errors.append(f"{file.relative_to(ROOT)}: broken link {target}")
    return errors


def main() -> int:
    """Print package verification results.

    Example:
        python3 scripts/validate_package.py
    """
    errors = validate()
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(
        "PASS: skill metadata, Python syntax, local Markdown links, distributable privacy patterns, shared model policy"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
