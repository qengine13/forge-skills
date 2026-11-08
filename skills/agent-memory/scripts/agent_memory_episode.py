#!/usr/bin/env python3
# Public edition adapted from the owner's Agent Memory server; see NOTICE.
from __future__ import annotations
from memory_paths import memory_path

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_MEMORY_LEDGER = Path(memory_path('episodes.jsonl'))
SCHEMA_VERSION = "agent-memory-episode.v1"
VALID_SCOPES = {"global", "project", "session"}


class AgentMemoryEpisodeError(Exception):
    """Raised when an agent memory episode is invalid."""


@dataclass(frozen=True, slots=True)
class AgentMemoryEpisode:
    schema_version: str
    episode_id: str
    created_at_ms: int
    scope: str
    project_path: str
    project_slug: str
    source: str
    summary: str
    decisions: list[str]
    supersedes: list[str]
    files_touched: list[str]
    commands: list[str]
    verification: list[str]
    open_questions: list[str]
    risks: list[str]
    next_steps: list[str]
    tags: list[str]


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def normalize_slug(project_path: Path) -> str:
    name = project_path.resolve().name.strip().lower()
    safe = "".join(char if char.isalnum() else "-" for char in name)
    return "-".join(part for part in safe.split("-") if part)


def validate_non_empty(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise AgentMemoryEpisodeError(f"{field_name} cannot be empty")
    return normalized


def clean_items(values: list[str]) -> list[str]:
    return [value.strip() for value in values if value.strip()]


def build_episode(args: argparse.Namespace) -> AgentMemoryEpisode:
    project_path = Path(args.project).expanduser().resolve()
    scope = validate_non_empty(args.scope, "scope")
    if scope not in VALID_SCOPES:
        raise AgentMemoryEpisodeError(
            f"scope must be one of {', '.join(sorted(VALID_SCOPES))}"
        )

    summary = validate_non_empty(args.summary, "summary")
    source = validate_non_empty(args.source, "source")
    project_slug = args.project_slug.strip() if args.project_slug else normalize_slug(project_path)

    if not project_slug:
        raise AgentMemoryEpisodeError("project_slug cannot be empty")

    return AgentMemoryEpisode(
        schema_version=SCHEMA_VERSION,
        episode_id=str(uuid.uuid4()),
        created_at_ms=now_ms(),
        scope=scope,
        project_path=str(project_path),
        project_slug=project_slug,
        source=source,
        summary=summary,
        decisions=clean_items(args.decision),
        supersedes=clean_items(args.supersedes),
        files_touched=clean_items(args.file),
        commands=clean_items(args.command),
        verification=clean_items(args.verification),
        open_questions=clean_items(args.open_question),
        risks=clean_items(args.risk),
        next_steps=clean_items(args.next_step),
        tags=clean_items(args.tag),
    )


def validate_episode_payload(payload: dict[str, object]) -> None:
    required = {
        "schema_version",
        "episode_id",
        "created_at_ms",
        "scope",
        "project_path",
        "project_slug",
        "source",
        "summary",
    }
    missing = sorted(required.difference(payload))
    if missing:
        raise AgentMemoryEpisodeError(f"missing required fields: {', '.join(missing)}")

    if payload["schema_version"] != SCHEMA_VERSION:
        raise AgentMemoryEpisodeError(
            f"unsupported schema_version: {payload['schema_version']}"
        )
    if payload["scope"] not in VALID_SCOPES:
        raise AgentMemoryEpisodeError(f"invalid scope: {payload['scope']}")
    if not str(payload["summary"]).strip():
        raise AgentMemoryEpisodeError("summary cannot be empty")


def memory_ledger_path(args: argparse.Namespace) -> Path:
    explicit_path = args.output or os.environ.get("AGENT_MEMORY_LEDGER")
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()
    return DEFAULT_MEMORY_LEDGER.expanduser().resolve()


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def capture(args: argparse.Namespace) -> int:
    episode = build_episode(args)
    payload = asdict(episode)
    validate_episode_payload(payload)

    if args.dry_run:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    path = memory_ledger_path(args)
    append_jsonl(path, payload)
    print(f"wrote episode_id={episode.episode_id} path={path}")
    return 0


def validate_file(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise AgentMemoryEpisodeError(
                    f"{path}:{line_number}: invalid JSON: {exc}"
                ) from exc
            validate_episode_payload(payload)
            count += 1

    print(f"validated {count} episodes from {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture validated structured episodes for agent memory."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--summary", required=True)
    capture_parser.add_argument("--scope", choices=sorted(VALID_SCOPES), default="project")
    capture_parser.add_argument("--project", default=os.getcwd())
    capture_parser.add_argument("--project-slug", default="")
    capture_parser.add_argument("--source", default="codex")
    capture_parser.add_argument("--decision", action="append", default=[])
    capture_parser.add_argument("--supersedes", action="append", default=[])
    capture_parser.add_argument("--file", action="append", default=[])
    capture_parser.add_argument("--command", action="append", default=[])
    capture_parser.add_argument("--verification", action="append", default=[])
    capture_parser.add_argument("--open-question", action="append", default=[])
    capture_parser.add_argument("--risk", action="append", default=[])
    capture_parser.add_argument("--next-step", action="append", default=[])
    capture_parser.add_argument("--tag", action="append", default=[])
    capture_parser.add_argument("--output")
    capture_parser.add_argument("--dry-run", action="store_true")
    capture_parser.set_defaults(func=capture)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("path")
    validate_parser.set_defaults(func=validate_file)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AgentMemoryEpisodeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
