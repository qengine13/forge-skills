#!/usr/bin/env python3
# Public edition adapted from the owner's Agent Memory server; see NOTICE.
from __future__ import annotations
from memory_paths import memory_path

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_GUARD_PID_FILE = Path(memory_path('resource.pid'))


class ResourceGuardError(Exception):
    """Raised when a resource guard check cannot safely pass."""


@dataclass(frozen=True, slots=True)
class ProcessStatus:
    active: bool
    detail: str


def pid_is_active(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def guard_pid_status(pid_file: Path) -> ProcessStatus:
    if not pid_file.exists():
        return ProcessStatus(active=False, detail=f"pid_file_missing:{pid_file}")
    raw_pid = pid_file.read_text(encoding="utf-8").strip()
    if not raw_pid:
        return ProcessStatus(active=False, detail=f"pid_file_empty:{pid_file}")
    try:
        pid = int(raw_pid)
    except ValueError:
        return ProcessStatus(active=True, detail=f"pid_file_invalid:{pid_file}:{raw_pid}")
    if pid_is_active(pid):
        return ProcessStatus(active=True, detail=f"guard_pid_active:{pid}")
    return ProcessStatus(active=False, detail=f"guard_pid_stale:{pid}")


def guard_status(pid_file: Path) -> ProcessStatus:
    return guard_pid_status(pid_file)


def assert_guard_inactive(pid_file: Path, force: bool) -> ProcessStatus:
    status = guard_status(pid_file)
    if status.active and not force:
        raise ResourceGuardError(
            "Configured resource guard process appears active; refusing local memory model work. "
            f"detail={status.detail}. Pass --force only for an intentional headroom test."
        )
    return status


def check(args: argparse.Namespace) -> int:
    status = assert_guard_inactive(Path(args.guard_pid_file), args.force)
    if status.active:
        print(f"guard_active=1 detail={status.detail}")
    else:
        print(f"guard_active=0 detail={status.detail}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Guard local memory model work from running alongside a configured resource-heavy process."
    )
    parser.add_argument("--guard-pid-file", default=str(DEFAULT_GUARD_PID_FILE))
    parser.add_argument("--force", action="store_true")
    parser.set_defaults(func=check)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ResourceGuardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
