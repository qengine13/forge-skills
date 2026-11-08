#!/usr/bin/env python3
# Public edition adapted from the owner's Agent Memory server; see NOTICE.
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


CODEX_HISTORY = Path("~/.codex/history.jsonl")
CODEX_SESSION_INDEX = Path("~/.codex/session_index.jsonl")
CODEX_STATE = Path("~/.codex/state_5.sqlite")
CLAUDE_PROJECTS = Path("~/.claude/projects")


@dataclass(frozen=True, slots=True)
class SessionCandidate:
    source: str
    session_id: str
    project_hint: str
    path: str
    updated_at: str
    message_count: int
    user_message_count: int
    preview: str


def parse_jsonl(path: Path, limit: int | None = None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
            if limit is not None and len(rows) >= limit:
                break
    return rows


def timestamp_from_epoch(value: object) -> str:
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def claude_projects_prefix(home: Path | None = None) -> str:
    """Return the folder-name prefix Claude Code uses for projects kept under a home directory.

    Claude Code encodes an absolute project path by replacing its separators with hyphens, so the
    home-directory part of each project folder name is derived from the running user's home.
    """
    base = (home or Path.home()).expanduser().resolve()
    return re.sub(r"[^A-Za-z0-9]", "-", str(base).strip("/\\")) + "-projects-"


def project_slug_from_claude_dir(path: Path, home: Path | None = None) -> str:
    name = path.name.strip("-")
    prefix = claude_projects_prefix(home)
    if name.startswith(prefix):
        return name.removeprefix(prefix).replace("-", "/")
    return name.replace("-", "/")


def scan_codex_history(home: Path, project_filter: str | None, limit: int) -> list[SessionCandidate]:
    history_path = (home / ".codex" / "history.jsonl").expanduser()
    rows = parse_jsonl(history_path)
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        session_id = str(row.get("session_id", ""))
        if not session_id:
            continue
        grouped[session_id].append(row)

    candidates: list[SessionCandidate] = []
    for session_id, messages in grouped.items():
        text_values = [str(message.get("text", "")) for message in messages]
        joined = "\n".join(text_values)
        if project_filter and project_filter.lower() not in joined.lower():
            continue
        latest_ts = max((message.get("ts", 0) for message in messages), default=0)
        candidates.append(
            SessionCandidate(
                source="codex",
                session_id=session_id,
                project_hint=project_filter or "unknown",
                path=str(history_path),
                updated_at=timestamp_from_epoch(latest_ts),
                message_count=len(messages),
                user_message_count=len(messages),
                preview=text_values[-1][:240] if text_values else "",
            )
        )

    candidates.sort(key=lambda item: item.updated_at, reverse=True)
    return candidates[:limit]


def text_matches_filter(text: str, project_filter: str | None) -> bool:
    if not project_filter:
        return True
    return project_filter.lower() in text.lower()


def scan_codex_session_index(
    home: Path,
    project_filter: str | None,
    limit: int,
) -> list[SessionCandidate]:
    index_path = (home / ".codex" / "session_index.jsonl").expanduser()
    rows = parse_jsonl(index_path)
    candidates: list[SessionCandidate] = []
    seen: set[str] = set()
    for row in reversed(rows):
        session_id = str(row.get("id", ""))
        if not session_id or session_id in seen:
            continue
        thread_name = str(row.get("thread_name", ""))
        if not text_matches_filter(thread_name, project_filter):
            continue
        seen.add(session_id)
        candidates.append(
            SessionCandidate(
                source="codex-index",
                session_id=session_id,
                project_hint=project_filter or "unknown",
                path=str(index_path),
                updated_at=str(row.get("updated_at", "")),
                message_count=0,
                user_message_count=0,
                preview=thread_name[:240],
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def claude_message_text(row: dict[str, object]) -> str:
    if row.get("type") not in {"user", "assistant"}:
        return ""
    message = row.get("message")
    if isinstance(message, dict):
        content = message.get("content", "")
    else:
        content = row.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts)
    return ""


def scan_claude_projects(home: Path, project_filter: str | None, limit: int) -> list[SessionCandidate]:
    projects_dir = (home / ".claude" / "projects").expanduser()
    if not projects_dir.exists():
        return []

    candidates: list[SessionCandidate] = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        project_hint = project_slug_from_claude_dir(project_dir, home)
        for path in project_dir.glob("*.jsonl"):
            rows = parse_jsonl(path)
            texts = [claude_message_text(row) for row in rows]
            texts = [text for text in texts if text.strip()]
            joined = "\n".join(texts)
            if project_filter and (
                project_filter.lower() not in project_hint.lower()
                and project_filter.lower() not in joined.lower()
            ):
                continue
            timestamps = [str(row.get("timestamp", "")) for row in rows if row.get("timestamp")]
            candidates.append(
                SessionCandidate(
                    source="claude",
                    session_id=path.stem,
                    project_hint=project_hint,
                    path=str(path),
                    updated_at=max(timestamps) if timestamps else "",
                    message_count=len(rows),
                    user_message_count=sum(1 for row in rows if row.get("type") == "user"),
                    preview=texts[-1][:240] if texts else "",
                )
            )

    candidates.sort(key=lambda item: item.updated_at, reverse=True)
    return candidates[:limit]


def print_candidates(candidates: list[SessionCandidate]) -> None:
    if not candidates:
        print("No session candidates found.")
        return
    for candidate in candidates:
        print(
            f"{candidate.updated_at} {candidate.source} {candidate.session_id} "
            f"project={candidate.project_hint} messages={candidate.message_count} "
            f"user={candidate.user_message_count} path={candidate.path}"
        )
        if candidate.preview:
            print(f"  preview={candidate.preview.replace(chr(10), ' ')[:240]}")


def summary(home: Path) -> int:
    counts = Counter()
    counts["codex_history_rows"] = len(parse_jsonl(home / ".codex" / "history.jsonl"))
    counts["codex_session_index_rows"] = len(
        parse_jsonl(home / ".codex" / "session_index.jsonl")
    )
    claude_projects = home / ".claude" / "projects"
    if claude_projects.exists():
        counts["claude_project_dirs"] = sum(1 for path in claude_projects.iterdir() if path.is_dir())
        counts["claude_jsonl_files"] = sum(1 for _ in claude_projects.glob("*/*.jsonl"))
    state = home / ".codex" / "state_5.sqlite"
    if state.exists():
        try:
            conn = sqlite3.connect(str(state))
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            counts["codex_state_tables"] = len(tables)
        except sqlite3.Error:
            counts["codex_state_tables"] = -1
    for key, value in sorted(counts.items()):
        print(f"{key}={value}")
    return 0


def list_sessions(args: argparse.Namespace) -> int:
    home = Path(args.home).expanduser()
    candidates: list[SessionCandidate] = []
    if args.source in {"all", "codex"}:
        candidates.extend(scan_codex_history(home, args.project, args.limit))
    if args.source in {"all", "codex-index"}:
        candidates.extend(scan_codex_session_index(home, args.project, args.limit))
    if args.source in {"all", "claude"}:
        candidates.extend(scan_claude_projects(home, args.project, args.limit))
    candidates.sort(key=lambda item: item.updated_at, reverse=True)
    candidates = candidates[: args.limit]
    if args.json:
        print(json.dumps([asdict(candidate) for candidate in candidates], indent=2))
    else:
        print_candidates(candidates)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory local Codex and Claude conversation history for memory import."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary_parser = subparsers.add_parser("summary")
    summary_parser.add_argument("--home", default=str(Path.home()))
    summary_parser.set_defaults(func=lambda args: summary(Path(args.home).expanduser()))

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--home", default=str(Path.home()))
    list_parser.add_argument("--source", choices=["all", "codex", "codex-index", "claude"], default="all")
    list_parser.add_argument("--project")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=list_sessions)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
