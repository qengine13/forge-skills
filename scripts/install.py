#!/usr/bin/env python3
"""Copy selected skills into a project without replacing existing files."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "skills"
DESTINATIONS = {"claude": ".claude/skills", "codex": ".agents/skills"}
DEPENDENCIES = {"project-forge": {"anti-slop-gates"}}


def install(project: Path, agent: str, names: list[str], dry_run: bool = False) -> list[Path]:
    """Install complete folders, refusing collisions before copying any skill.

    Example:
        install(Path('/tmp/demo'), 'claude', ['plan-hygiene'])
    """
    project = project.expanduser().resolve()
    if not project.is_dir():
        raise ValueError(f"Project directory does not exist: {project}")
    selected = set(names)
    for name in names:
        selected.update(DEPENDENCIES.get(name, set()))
    available = {p.name for p in SOURCE.iterdir() if (p / "SKILL.md").is_file()}
    if not selected or selected - available:
        raise ValueError("Select existing skills; use --help to list them.")
    target = project / DESTINATIONS[agent]
    for parent in (target.parent, target):
        if parent.is_symlink():
            raise ValueError(f"Refusing a symlinked installation directory: {parent}")
        if parent.exists() and not parent.is_dir():
            raise ValueError(f"Installation path is not a directory: {parent}")
    destinations = [target / name for name in sorted(selected)]
    conflicts = [str(p) for p in destinations if p.exists() or p.is_symlink()]
    if conflicts:
        raise ValueError("Nothing installed. Existing skill(s): " + ", ".join(conflicts))
    if not dry_run:
        for destination in destinations:
            shutil.copytree(
                SOURCE / destination.name,
                destination,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
            )
    return destinations


def main() -> int:
    """Parse installation options and report the exact copied directories.

    Example:
        python3 scripts/install.py --agent claude --project /tmp/demo --all --dry-run
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, choices=DESTINATIONS)
    parser.add_argument("--project", required=True, type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--skill", action="append", choices=sorted(p.name for p in SOURCE.iterdir()))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    names = sorted(p.name for p in SOURCE.iterdir()) if args.all else args.skill
    try:
        paths = install(args.project, args.agent, names, args.dry_run)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
    for path in paths:
        print(f"{'Would install' if args.dry_run else 'Installed'}: {path}")
    print("Start a new agent session in that project, then invoke a skill by name.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
