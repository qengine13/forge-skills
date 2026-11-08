#!/usr/bin/env python3
# Public edition adapted from the owner's Agent Memory server; see NOTICE.
from __future__ import annotations
from memory_paths import memory_path

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_memory_episode import (
    AgentMemoryEpisode,
    AgentMemoryEpisodeError,
    SCHEMA_VERSION,
    append_jsonl,
    clean_items,
    normalize_slug,
    now_ms,
    validate_episode_payload,
)
from agent_memory_history_scan import (
    claude_message_text,
    parse_jsonl,
    project_slug_from_claude_dir,
)
from agent_memory_postgres import (
    AgentMemoryPostgresError,
    default_dsn,
    upsert_episode,
)


DEFAULT_MEMORY_LEDGER = Path(memory_path('episodes.jsonl'))
DEFAULT_MAX_ITEMS = 8
MAX_EXCERPT_CHARS = 240
MAX_SUMMARY_CHARS = 700
MIN_SIGNAL_CHARS = 24

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[^'\"\s]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
]

COMMAND_PREFIXES = (
    "python",
    "python3",
    "pytest",
    "uv ",
    "pnpm",
    "npm",
    "yarn",
    "git",
    "rg",
    "sqlite3",
    "psql",
    "createdb",
    "cargo",
    "node",
    "npx",
)

PATH_RE = re.compile(
    r"(?<![\w/.-])(?:[./~]?[\w.-]+/)+[\w.-]+\.(?:py|md|json|jsonl|toml|yaml|yml|ts|tsx|js|jsx|rs|sql|sh|txt)"
)


class AgentMemoryRetroImportError(Exception):
    """Raised when a historical session cannot be drafted or captured safely."""


@dataclass(frozen=True, slots=True)
class TranscriptMessage:
    role: str
    text: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class RetroImportDraft:
    episode: dict[str, object]
    source: str
    session_id: str
    session_path: str
    message_count: int
    user_message_count: int
    assistant_message_count: int
    excerpts: list[dict[str, str]]
    capture_ready: bool
    warning: str


def redact(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: match.group(0).split("=", 1)[0] + "=[REDACTED]", redacted)
    return redacted


def normalize_text(text: str) -> str:
    return " ".join(redact(text).split())


def excerpt(text: str, limit: int = MAX_EXCERPT_CHARS) -> str:
    normalized = normalize_text(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def load_codex_messages(home: Path, session_id: str) -> tuple[list[TranscriptMessage], Path]:
    history_path = home / ".codex" / "history.jsonl"
    rows = [
        row for row in parse_jsonl(history_path)
        if str(row.get("session_id", "")) == session_id
    ]
    messages = [
        TranscriptMessage(
            role="user",
            text=str(row.get("text", "")),
            timestamp=str(row.get("ts", "")),
        )
        for row in rows
        if str(row.get("text", "")).strip()
    ]
    if not messages:
        raise AgentMemoryRetroImportError(f"codex session not found: {session_id}")
    return messages, history_path


def project_slug_from_claude_project_dir(path: Path, home: Path | None = None) -> str:
    return project_slug_from_claude_dir(path, home)


def find_claude_session_path(home: Path, session_id: str, project_filter: str | None) -> Path:
    projects_dir = home / ".claude" / "projects"
    if not projects_dir.exists():
        raise AgentMemoryRetroImportError(f"Claude projects directory does not exist: {projects_dir}")
    matches: list[Path] = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        project_hint = project_slug_from_claude_project_dir(project_dir, home)
        if project_filter and project_filter.lower() not in project_hint.lower():
            continue
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate.exists():
            matches.append(candidate)
    if not matches:
        raise AgentMemoryRetroImportError(f"claude session not found: {session_id}")
    if len(matches) > 1:
        joined = ", ".join(str(path) for path in matches)
        raise AgentMemoryRetroImportError(f"multiple Claude sessions matched; add --project-filter: {joined}")
    return matches[0]


def load_claude_messages(
    home: Path,
    session_id: str,
    project_filter: str | None,
) -> tuple[list[TranscriptMessage], Path]:
    session_path = find_claude_session_path(home, session_id, project_filter)
    messages: list[TranscriptMessage] = []
    for row in parse_jsonl(session_path):
        text = claude_message_text(row)
        if not text.strip():
            continue
        messages.append(
            TranscriptMessage(
                role=str(row.get("type", "")),
                text=text,
                timestamp=str(row.get("timestamp", "")),
            )
        )
    if not messages:
        raise AgentMemoryRetroImportError(f"claude session has no text messages: {session_id}")
    return messages, session_path


def load_messages(
    source: str,
    home: Path,
    session_id: str,
    project_filter: str | None,
) -> tuple[list[TranscriptMessage], Path]:
    if source == "codex":
        return load_codex_messages(home, session_id)
    if source == "claude":
        return load_claude_messages(home, session_id, project_filter)
    raise AgentMemoryRetroImportError("source must be codex or claude")


def signal_lines(messages: list[TranscriptMessage]) -> list[str]:
    lines: list[str] = []
    for message in messages:
        for raw_line in message.text.splitlines():
            line = normalize_text(raw_line.strip(" -\t"))
            if len(line) >= MIN_SIGNAL_CHARS:
                lines.append(line)
    return lines


def unique_limited(values: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = excerpt(value)
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if len(result) >= limit:
            break
    return result


def extract_commands(lines: list[str], limit: int) -> list[str]:
    commands: list[str] = []
    for line in lines:
        stripped = line.strip("$` ")
        if stripped.startswith(COMMAND_PREFIXES):
            commands.append(stripped)
    return unique_limited(commands, limit)


def extract_files(lines: list[str], limit: int) -> list[str]:
    paths: list[str] = []
    for line in lines:
        paths.extend(match.group(0) for match in PATH_RE.finditer(line))
    return unique_limited(paths, limit)


def extract_keyword_lines(lines: list[str], keywords: tuple[str, ...], limit: int) -> list[str]:
    hits = [
        line for line in lines
        if any(keyword in line.lower() for keyword in keywords)
    ]
    return unique_limited(hits, limit)


def extract_excerpts(messages: list[TranscriptMessage], limit: int) -> list[dict[str, str]]:
    excerpts: list[dict[str, str]] = []
    for message in messages:
        text = excerpt(message.text)
        if not text:
            continue
        excerpts.append(
            {
                "role": message.role,
                "timestamp": message.timestamp,
                "text": text,
            }
        )
        if len(excerpts) >= limit:
            break
    return excerpts


def first_user_request(messages: list[TranscriptMessage]) -> str:
    for message in messages:
        if message.role == "user" and message.text.strip():
            return excerpt(message.text, 320)
    return excerpt(messages[0].text, 320)


def build_summary(source: str, session_id: str, messages: list[TranscriptMessage]) -> str:
    summary = (
        f"Retroactive {source} session import candidate {session_id}. "
        f"First user request: {first_user_request(messages)}. "
        "Generated by heuristic extraction from a selected local history file; "
        "verify exact project files before treating inferred details as current truth."
    )
    if len(summary) <= MAX_SUMMARY_CHARS:
        return summary
    return summary[: MAX_SUMMARY_CHARS - 3].rstrip() + "..."


def build_episode_payload(
    *,
    source: str,
    session_id: str,
    session_path: Path,
    messages: list[TranscriptMessage],
    project_path: Path,
    project_slug: str,
    scope: str,
    max_items: int,
) -> dict[str, object]:
    lines = signal_lines(messages)
    decisions = extract_keyword_lines(
        lines,
        ("decision", "decided", "canonical", "must", "should", "contract", "scope"),
        max_items,
    )
    risks = extract_keyword_lines(
        lines,
        ("risk", "blocker", "failed", "failure", "bug", "regression", "warning"),
        max_items,
    )
    next_steps = extract_keyword_lines(
        lines,
        ("next step", "todo", "follow-up", "remaining", "still open", "open question"),
        max_items,
    )
    episode = AgentMemoryEpisode(
        schema_version=SCHEMA_VERSION,
        episode_id="",
        created_at_ms=now_ms(),
        scope=scope,
        project_path=str(project_path),
        project_slug=project_slug,
        source=f"retro-import:{source}",
        summary=build_summary(source, session_id, messages),
        decisions=decisions,
        supersedes=[],
        files_touched=extract_files(lines, max_items),
        commands=extract_commands(lines, max_items),
        verification=extract_keyword_lines(
            lines,
            ("verified", "verification", "passed", "exit code", "tests pass", "green"),
            max_items,
        ),
        open_questions=extract_keyword_lines(
            lines,
            ("open question", "unclear", "unknown", "still open", "needs decision"),
            max_items,
        ),
        risks=risks,
        next_steps=next_steps,
        tags=clean_items([
            "retro-import",
            "history-scan",
            source,
            f"session:{session_id}",
            "needs-review",
            f"path:{session_path.name}",
        ]),
    )
    payload = asdict(episode)
    import uuid

    payload["episode_id"] = str(uuid.uuid4())
    validate_episode_payload(payload)
    return payload


def default_project_path(raw_project_path: str | None) -> Path:
    if raw_project_path:
        return Path(raw_project_path).expanduser().resolve()
    return Path.cwd().resolve()


def default_project_slug(raw_project_slug: str | None, project_path: Path) -> str:
    if raw_project_slug:
        return raw_project_slug.strip()
    return normalize_slug(project_path)


def postgres_enabled() -> bool:
    flag = os.environ.get("AGENT_MEMORY_USE_POSTGRES", "").strip().lower()
    return bool(os.environ.get("AGENT_MEMORY_POSTGRES_DSN")) or flag in {
        "1",
        "true",
        "yes",
        "on",
    }


def ledger_path(raw_output: str | None) -> Path:
    explicit = raw_output or os.environ.get("AGENT_MEMORY_LEDGER")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return DEFAULT_MEMORY_LEDGER.expanduser().resolve()


def draft_session(args: argparse.Namespace) -> RetroImportDraft:
    home = Path(args.home).expanduser()
    messages, session_path = load_messages(
        args.source,
        home,
        args.session_id,
        args.project_filter,
    )
    project_path = default_project_path(args.project_path)
    project_slug = default_project_slug(args.project_slug, project_path)
    episode = build_episode_payload(
        source=args.source,
        session_id=args.session_id,
        session_path=session_path,
        messages=messages,
        project_path=project_path,
        project_slug=project_slug,
        scope=args.scope,
        max_items=args.max_items,
    )
    return RetroImportDraft(
        episode=episode,
        source=args.source,
        session_id=args.session_id,
        session_path=str(session_path),
        message_count=len(messages),
        user_message_count=sum(1 for message in messages if message.role == "user"),
        assistant_message_count=sum(1 for message in messages if message.role == "assistant"),
        excerpts=extract_excerpts(messages, args.excerpt_count),
        capture_ready=bool(args.reviewed),
        warning=(
            "Draft only. Capture requires --capture and should be used after reviewing "
            "the generated episode for stale or sensitive content."
        ),
    )


def run_draft(args: argparse.Namespace) -> int:
    draft = draft_session(args)
    payload = asdict(draft)
    if args.capture:
        if not args.reviewed:
            raise AgentMemoryRetroImportError("--capture requires --reviewed")
        path = ledger_path(args.ledger)
        append_jsonl(path, draft.episode)
        payload["captured"] = {"ledger": str(path), "backend": "jsonl"}
        if postgres_enabled():
            try:
                upsert_episode(default_dsn(), draft.episode)
                payload["captured"]["backend"] = "jsonl+postgres"
            except AgentMemoryPostgresError as exc:
                payload["captured"]["postgres_error"] = str(exc)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Draft safe structured memory episodes from selected historical sessions."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    draft_parser = subparsers.add_parser("draft")
    draft_parser.add_argument("--source", choices=["codex", "claude"], required=True)
    draft_parser.add_argument("--session-id", required=True)
    draft_parser.add_argument("--project-filter")
    draft_parser.add_argument("--project-path")
    draft_parser.add_argument("--project-slug")
    draft_parser.add_argument("--scope", choices=["global", "project", "session"], default="project")
    draft_parser.add_argument("--home", default=str(Path.home()))
    draft_parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    draft_parser.add_argument("--excerpt-count", type=int, default=4)
    draft_parser.add_argument("--capture", action="store_true")
    draft_parser.add_argument("--reviewed", action="store_true")
    draft_parser.add_argument("--ledger")
    draft_parser.set_defaults(func=run_draft)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (
        AgentMemoryRetroImportError,
        AgentMemoryEpisodeError,
        AgentMemoryPostgresError,
        OSError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
