#!/usr/bin/env python3
# Public edition adapted from the owner's Agent Memory server; see NOTICE.
from __future__ import annotations
from memory_paths import memory_path

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agent_memory_embed_index import DEFAULT_INDEX, DEFAULT_LEDGER, episode_text
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
    scan_claude_projects,
    scan_codex_history,
    scan_codex_session_index,
)
from agent_memory_postgres import (
    AgentMemoryPostgresError,
    default_dsn,
    fetch_episodes,
    fetch_recent,
    fetch_recent_excluding,
    fetch_stats,
    upsert_episode,
)
from agent_memory_resource_guard import ResourceGuardError, assert_guard_inactive
from agent_memory_retro_import import (
    AgentMemoryRetroImportError,
    draft_session,
    ledger_path as retro_ledger_path,
    postgres_enabled as retro_postgres_enabled,
)
from agent_memory_dream import (
    AgentMemoryDreamError,
    apply_plan_payload as dream_apply_plan_payload,
    build_plan as dream_build_plan,
    set_apply_defaults as dream_set_apply_defaults,
)
from agent_memory_doctrine_audit import (
    AUDIT_PROJECT_SLUG,
    AgentMemoryDoctrineAuditError,
    is_run_snapshot,
    latest_snapshot_digest,
    render_trend_digest,
    run_audit as run_doctrine_audit,
)
from agent_memory_heal import (
    AgentMemoryHealError,
    apply_heal_plan as heal_apply_plan,
    plan_heal_actions as heal_plan_actions,
)
from agent_memory_lessons import recall_keyword


SERVER_NAME = "forge-agent-memory"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-06-18"
DEFAULT_MAX_CHARS = 12000
MAX_TOOL_LIMIT = 50

PROJECT_TRUTH_FILES = {
    "identity": "identity.md",
    "agents": "AGENTS.md",
    "progress": "progress.md",
    "anomalies": "docs/ANOMALIES.md",
    "task": "task.md",
    "style": "STYLE_GUIDE.md",
    "memory_doc": "docs/agent-memory.md",
}


class McpError(Exception):
    """Raised when an MCP tool cannot complete."""


def text_result(text: str, is_error: bool = False) -> dict[str, object]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def json_text(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def ledger_path() -> Path:
    return Path(DEFAULT_LEDGER).expanduser().resolve()


def index_path() -> Path:
    return Path(DEFAULT_INDEX).expanduser().resolve()


def postgres_enabled() -> bool:
    flag = os.environ.get("AGENT_MEMORY_USE_POSTGRES", "").strip().lower()
    return bool(os.environ.get("AGENT_MEMORY_POSTGRES_DSN")) or flag in {
        "1",
        "true",
        "yes",
        "on",
    }


def connect_index_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise McpError(f"memory index does not exist: {path}")
    uri = f"file:{path}?mode=ro&immutable=1"
    return sqlite3.connect(uri, timeout=10.0, uri=True)


def clamp_limit(value: object, default: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, MAX_TOOL_LIMIT))


def filter_default_memory_rows(
    rows: list[dict[str, object]],
    project_slug: str | None,
) -> list[dict[str, object]]:
    if project_slug:
        return rows
    return [
        row for row in rows
        if row.get("project_slug") != AUDIT_PROJECT_SLUG
    ]


def audit_episode_tags(episode: dict[str, object]) -> list[str]:
    tags = episode.get("tags", [])
    if not isinstance(tags, list):
        return []
    return [str(tag) for tag in tags]


def audit_tag_value(episode: dict[str, object], prefix: str) -> str:
    for tag in audit_episode_tags(episode):
        if tag.startswith(prefix):
            return tag.split(":", 1)[1]
    return ""


def audit_status(episode: dict[str, object]) -> str:
    audit = episode.get("audit")
    if isinstance(audit, dict) and audit.get("status"):
        return str(audit["status"])
    return audit_tag_value(episode, "status:") or "open"


def audit_run_id(episode: dict[str, object]) -> str:
    audit = episode.get("audit")
    if isinstance(audit, dict) and audit.get("run_id"):
        return str(audit["run_id"])
    return audit_tag_value(episode, "run:")


def audit_first_seen_ms(episode: dict[str, object]) -> int:
    audit = episode.get("audit")
    value = audit.get("first_seen_at_ms") if isinstance(audit, dict) else None
    if value is None:
        value = episode.get("created_at_ms", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def audit_updated_ms(episode: dict[str, object]) -> int:
    audit = episode.get("audit")
    value = audit.get("updated_at_ms") if isinstance(audit, dict) else None
    if value is None:
        value = episode.get("created_at_ms", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def episode_created_at_ms(episode: dict[str, object]) -> int:
    try:
        return int(episode.get("created_at_ms", 0))
    except (TypeError, ValueError):
        return 0


def prior_status_copy(episode: dict[str, object], status: str) -> dict[str, object]:
    payload = dict(episode)
    tags = [
        tag for tag in audit_episode_tags(payload)
        if not tag.startswith("status:")
    ]
    tags.append(f"status:{status}")
    payload["tags"] = tags
    audit = payload.get("audit")
    if isinstance(audit, dict):
        audit_copy = dict(audit)
        audit_copy["status"] = status
        payload["audit"] = audit_copy
    return payload


def global_finding_trend_windows(
    episodes: list[dict[str, object]],
    latest_run: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if not episodes:
        return [], []
    prior: list[dict[str, object]] = []
    for episode in episodes:
        status = audit_status(episode)
        is_latest_run = audit_run_id(episode) == latest_run
        first_seen = audit_first_seen_ms(episode)
        updated = audit_updated_ms(episode)
        if not is_latest_run:
            prior.append(prior_status_copy(episode, status))
        elif first_seen != updated:
            prior.append(prior_status_copy(episode, "open" if status == "resolved" else status))
    return prior, episodes


def latest_audit_run_id(episodes: list[dict[str, object]]) -> str:
    ranked = [
        episode for episode in episodes
        if audit_run_id(episode)
    ]
    if not ranked:
        return ""
    latest = max(
        ranked,
        key=lambda episode: (episode_created_at_ms(episode), audit_updated_ms(episode)),
    )
    return audit_run_id(latest)


def clamp_max_chars(value: object) -> int:
    try:
        max_chars = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_CHARS
    return max(1000, min(max_chars, 50000))


def resolve_project_root(raw_path: object | None) -> Path:
    if raw_path:
        return Path(str(raw_path)).expanduser().resolve()
    return Path.cwd().resolve()


def read_text_excerpt(path: Path, max_chars: int, tail: bool) -> dict[str, object]:
    if not path.exists():
        return {"path": str(path), "exists": False, "text": ""}
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    if truncated:
        text = text[-max_chars:] if tail else text[:max_chars]
    return {
        "path": str(path),
        "exists": True,
        "chars": len(text),
        "truncated": truncated,
        "tail": tail,
        "text": text,
    }


def load_recent_episodes(project_slug: str | None, limit: int) -> list[dict[str, object]]:
    path = ledger_path()
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if project_slug and payload.get("project_slug") != project_slug:
                continue
            if not project_slug and payload.get("project_slug") == AUDIT_PROJECT_SLUG:
                continue
            rows.append(payload)
    return rows[-limit:]


def keyword_search(query: str, project_slug: str | None, limit: int) -> list[dict[str, object]]:
    query_terms = [term.lower() for term in query.split() if term.strip()]
    if not query_terms:
        return []
    conn = connect_index_readonly(index_path())
    params: list[object] = []
    where = "1=1"
    if project_slug:
        where += " AND project_slug = ?"
        params.append(project_slug)
    else:
        where += " AND project_slug != ?"
        params.append(AUDIT_PROJECT_SLUG)
    rows = conn.execute(
        f"""
        SELECT episode_id, project_slug, scope, summary, tags_json, text
        FROM episode_embeddings
        WHERE {where}
        ORDER BY embedded_at_ms DESC
        """,
        params,
    ).fetchall()
    results: list[dict[str, object]] = []
    for row in rows:
        text = str(row[5]).lower()
        score = sum(1 for term in query_terms if term in text)
        if score == 0:
            continue
        results.append(
            {
                "score": score,
                "episode_id": row[0],
                "project_slug": row[1],
                "scope": row[2],
                "summary": row[3],
                "tags": json.loads(row[4]),
            }
        )
    results.sort(key=lambda item: int(item["score"]), reverse=True)
    return results[:limit]


def canonical_keyword_search(
    episodes: list[dict[str, object]],
    query: str,
    project_slug: str | None,
    limit: int,
) -> list[dict[str, object]]:
    """Rank current canonical episodes using deterministic keyword coverage.

    Episodes named by a newer episode's ``supersedes`` edge are historical and
    therefore excluded from normal recall. The ledger remains unchanged, so
    callers can still inspect that history explicitly through recent/list APIs.
    """
    terms = list(
        dict.fromkeys(
            term
            for term in re.findall(r"[a-z0-9][a-z0-9_.:/-]*", query.lower())
            if len(term) >= 3
        )
    )
    if not terms:
        return []

    superseded_ids = {
        str(older_id)
        for episode in episodes
        for older_id in episode.get("supersedes", [])
        if older_id
    }
    normalized_query = " ".join(terms)
    results: list[dict[str, object]] = []
    for episode in episodes:
        if project_slug and episode.get("project_slug") != project_slug:
            continue
        if not project_slug and episode.get("project_slug") == AUDIT_PROJECT_SLUG:
            continue
        if str(episode.get("episode_id", "")) in superseded_ids:
            continue

        text = episode_text(episode).lower()
        summary = str(episode.get("summary", "")).lower()
        matched_terms = [term for term in terms if term in text]
        if not matched_terms:
            continue
        summary_matches = sum(1 for term in matched_terms if term in summary)
        coverage = len(matched_terms) / len(terms)
        exact_bonus = 3.0 if normalized_query in " ".join(text.split()) else 0.0
        score = len(matched_terms) + coverage + (summary_matches * 0.25) + exact_bonus
        results.append(
            {
                "score": round(score, 3),
                "matched_terms": matched_terms,
                "episode_id": episode.get("episode_id"),
                "project_slug": episode.get("project_slug"),
                "scope": episode.get("scope"),
                "summary": episode.get("summary"),
                "tags": episode.get("tags", []),
                "created_at_ms": episode.get("created_at_ms"),
            }
        )
    results.sort(
        key=lambda item: (
            -float(item["score"]),
            -int(item.get("created_at_ms") or 0),
            str(item.get("episode_id") or ""),
        )
    )
    return results[:limit]


def tool_memory_search(arguments: dict[str, Any]) -> dict[str, object]:
    query = str(arguments.get("query", "")).strip()
    if not query:
        raise McpError("query is required")
    project_slug = arguments.get("project_slug")
    limit = clamp_limit(arguments.get("limit", 5), 5)
    normalized_project_slug = str(project_slug) if project_slug else None
    if postgres_enabled():
        try:
            episodes = fetch_episodes(
                default_dsn(),
                [normalized_project_slug] if normalized_project_slug else None,
                None,
            )
            results = canonical_keyword_search(
                episodes=episodes,
                query=query,
                project_slug=normalized_project_slug,
                limit=limit,
            )
            lessons = recall_keyword(query=query, k=limit)
            return text_result(
                json_text(
                    {
                        "results": results,
                        "lessons": lessons,
                        "mode": "canonical_keyword_with_lessons",
                        "backend": "postgres",
                    }
                )
            )
        except AgentMemoryPostgresError as exc:
            results = keyword_search(
                query=query,
                project_slug=normalized_project_slug,
                limit=limit,
            )
            return text_result(
                json_text(
                    {
                        "results": results,
                        "lessons": [],
                        "mode": "keyword_index_fallback",
                        "backend": "sqlite",
                        "postgres_error": str(exc),
                    }
                )
            )

    rows = load_recent_episodes(normalized_project_slug, 1000000)
    results = canonical_keyword_search(rows, query, normalized_project_slug, limit)
    return text_result(json_text({"results": results, "lessons": [], "mode": "canonical_keyword", "backend": "jsonl"}))


def tool_memory_recent(arguments: dict[str, Any]) -> dict[str, object]:
    project_slug = arguments.get("project_slug")
    limit = clamp_limit(arguments.get("limit", 10), 10)
    if postgres_enabled():
        try:
            if project_slug:
                rows = fetch_recent(default_dsn(), str(project_slug), limit)
            else:
                rows = fetch_recent_excluding(default_dsn(), AUDIT_PROJECT_SLUG, limit)
            return text_result(
                json_text({"episodes": rows, "backend": "postgres"})
            )
        except AgentMemoryPostgresError as exc:
            rows = load_recent_episodes(
                project_slug=str(project_slug) if project_slug else None,
                limit=limit,
            )
            return text_result(
                json_text(
                    {
                        "episodes": rows,
                        "backend": "jsonl",
                        "postgres_error": str(exc),
                    }
                )
            )
    rows = load_recent_episodes(
        project_slug=str(project_slug) if project_slug else None,
        limit=limit,
    )
    rows = filter_default_memory_rows(rows, str(project_slug) if project_slug else None)
    return text_result(json_text({"episodes": rows}))


def tool_memory_capture(arguments: dict[str, Any]) -> dict[str, object]:
    for key in ("decisions", "supersedes", "files_touched", "commands", "verification", "open_questions", "risks", "next_steps", "tags"):
        value = arguments.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise McpError(f"{key} must be an array of strings")
    summary = str(arguments.get("summary", "")).strip()
    if not summary:
        raise McpError("summary is required")
    project_path = Path(str(arguments.get("project_path", Path.cwd()))).expanduser().resolve()
    project_slug = str(arguments.get("project_slug") or normalize_slug(project_path))
    episode = AgentMemoryEpisode(
        schema_version=SCHEMA_VERSION,
        episode_id=str(arguments.get("episode_id") or ""),
        created_at_ms=now_ms(),
        scope=str(arguments.get("scope", "project")),
        project_path=str(project_path),
        project_slug=project_slug,
        source=str(arguments.get("source", "mcp")),
        summary=summary,
        decisions=clean_items(list(arguments.get("decisions", []))),
        supersedes=clean_items(list(arguments.get("supersedes", []))),
        files_touched=clean_items(list(arguments.get("files_touched", []))),
        commands=clean_items(list(arguments.get("commands", []))),
        verification=clean_items(list(arguments.get("verification", []))),
        open_questions=clean_items(list(arguments.get("open_questions", []))),
        risks=clean_items(list(arguments.get("risks", []))),
        next_steps=clean_items(list(arguments.get("next_steps", []))),
        tags=clean_items(list(arguments.get("tags", []))),
    )
    payload = asdict(episode)
    if not payload["episode_id"]:
        import uuid

        payload["episode_id"] = str(uuid.uuid4())
    validate_episode_payload(payload)
    existing = load_recent_episodes(None, 1000000)
    if postgres_enabled():
        existing = fetch_episodes(default_dsn(), None, None)
    by_id = {str(item.get("episode_id")): item for item in existing}
    if payload["episode_id"] in by_id:
        raise McpError("episode_id already exists; create a new episode to preserve history")
    for older_id in payload["supersedes"]:
        older = by_id.get(older_id)
        if older is None or older.get("project_slug") != project_slug:
            raise McpError("supersedes must reference an existing episode in the same project")
    append_jsonl(ledger_path(), payload)
    result: dict[str, object] = {
        "captured": payload["episode_id"],
        "path": str(ledger_path()),
        "backend": "jsonl",
    }
    if postgres_enabled():
        try:
            upsert_episode(default_dsn(), payload)
            result["postgres"] = "synced"
            result["backend"] = "jsonl+postgres"
        except AgentMemoryPostgresError as exc:
            result["postgres_error"] = str(exc)
    return text_result(json_text(result))


def tool_memory_guard(arguments: dict[str, Any]) -> dict[str, object]:
    force = bool(arguments.get("force", False))
    pid_file = Path(str(arguments.get("guard_pid_file", memory_path('resource.pid'))))
    try:
        status = assert_guard_inactive(pid_file, force)
    except ResourceGuardError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(json_text({"guard_active": status.active, "detail": status.detail}))


def tool_memory_stats(arguments: dict[str, Any]) -> dict[str, object]:
    postgres_stats: list[dict[str, object]] | None = None
    postgres_error: str | None = None
    if postgres_enabled():
        try:
            postgres_stats = fetch_stats(default_dsn())
        except AgentMemoryPostgresError as exc:
            postgres_error = str(exc)

    if not index_path().exists():
        return text_result(json_text({"backend": "postgres" if postgres_enabled() else "jsonl", "episodes": len(load_recent_episodes(None, 1000000)), "embedding_index": "not configured", "postgres": postgres_stats, "postgres_error": postgres_error}))
    conn = connect_index_readonly(index_path())
    rows = conn.execute(
        """
        SELECT project_slug, embedding_model, COUNT(*), MAX(embedded_at_ms)
        FROM episode_embeddings
        GROUP BY project_slug, embedding_model
        ORDER BY project_slug, embedding_model
        """
    ).fetchall()
    stats = [
        {
            "project_slug": row[0],
            "embedding_model": row[1],
            "episodes": row[2],
            "latest_embedded_at_ms": row[3],
        }
        for row in rows
    ]
    payload: dict[str, object] = {
        "index": str(index_path()),
        "stats": stats,
        "backend": "sqlite_index",
    }
    if postgres_stats is not None:
        payload["postgres"] = {"stats": postgres_stats}
    if postgres_error is not None:
        payload["postgres_error"] = postgres_error
    return text_result(json_text(payload))


def tool_history_scan(arguments: dict[str, Any]) -> dict[str, object]:
    home = Path(str(arguments.get("home", Path.home()))).expanduser()
    source = str(arguments.get("source", "all"))
    project = arguments.get("project")
    limit = clamp_limit(arguments.get("limit", 20), 20)
    candidates = []
    if source in {"all", "codex"}:
        candidates.extend(scan_codex_history(home, str(project) if project else None, limit))
    if source in {"all", "codex-index"}:
        candidates.extend(scan_codex_session_index(home, limit))
    if source in {"all", "claude"}:
        candidates.extend(scan_claude_projects(home, str(project) if project else None, limit))
    candidates.sort(key=lambda item: item.updated_at, reverse=True)
    return text_result(json_text([asdict(item) for item in candidates[:limit]]))


def tool_retro_import(arguments: dict[str, Any]) -> dict[str, object]:
    source = str(arguments.get("source", "")).strip()
    session_id = str(arguments.get("session_id", "")).strip()
    if source not in {"codex", "claude"}:
        raise McpError("source must be codex or claude")
    if not session_id:
        raise McpError("session_id is required")

    args = argparse.Namespace(
        source=source,
        session_id=session_id,
        project_filter=arguments.get("project_filter"),
        project_path=arguments.get("project_path"),
        project_slug=arguments.get("project_slug"),
        scope=str(arguments.get("scope", "project")),
        home=str(arguments.get("home", str(Path.home()))),
        max_items=clamp_limit(arguments.get("max_items", 8), 8),
        excerpt_count=clamp_limit(arguments.get("excerpt_count", 4), 4),
        reviewed=arguments.get("reviewed") is True,
    )
    draft = draft_session(args)
    payload = asdict(draft)
    if bool(arguments.get("capture", False)):
        if not args.reviewed:
            raise McpError("capture requires reviewed=true")
        path = retro_ledger_path(str(arguments.get("ledger")) if arguments.get("ledger") else None)
        append_jsonl(path, draft.episode)
        payload["captured"] = {"ledger": str(path), "backend": "jsonl"}
        if retro_postgres_enabled():
            try:
                upsert_episode(default_dsn(), draft.episode)
                payload["captured"]["backend"] = "jsonl+postgres"
            except AgentMemoryPostgresError as exc:
                payload["captured"]["postgres_error"] = str(exc)
    return text_result(json_text(payload))


def tool_dream_plan(arguments: dict[str, Any]) -> dict[str, object]:
    project_slug = str(arguments.get("project_slug", "")).strip()
    project_path = str(arguments.get("project_path", "")).strip()
    if not project_slug and not project_path:
        raise McpError("project_slug or project_path is required")
    args = argparse.Namespace(
        project_slug=project_slug or None,
        project_path=project_path or None,
        alias=list(arguments.get("alias", [])),
        alias_file=arguments.get("alias_file"),
        source=str(arguments.get("source", "auto")),
        ledger=str(arguments.get("ledger", DEFAULT_LEDGER)),
        index=str(arguments.get("index", DEFAULT_INDEX)),
        dsn=str(arguments.get("dsn", default_dsn())),
        run_root=str(arguments.get("run_root", memory_path('dream-runs'))),
        promote_threshold=clamp_limit(arguments.get("promote_threshold", 3), 3),
    )
    plan = dream_build_plan(args)
    return text_result(json_text(plan))


def tool_dream_apply(arguments: dict[str, Any]) -> dict[str, object]:
    plan_path = str(arguments.get("plan", "")).strip()
    if not plan_path:
        raise McpError("plan is required")
    args = argparse.Namespace(
        plan=plan_path,
        reviewed=arguments.get("reviewed") is True,
        dsn=str(arguments.get("dsn", default_dsn())),
        no_postgres=bool(arguments.get("no_postgres", False)),
    )
    return text_result(json_text(dream_apply_plan_payload(dream_set_apply_defaults(args))))


def tool_memory_heal_plan(arguments: dict[str, Any]) -> dict[str, object]:
    project_slug = str(arguments.get("project_slug", "")).strip() or None
    try:
        limit_raw = arguments.get("limit")
        limit = int(limit_raw) if limit_raw is not None else None
    except (TypeError, ValueError):
        limit = None
    model = str(arguments.get("model", "nomic-embed-text"))
    ollama_url = str(arguments.get("ollama_url", "http://127.0.0.1:11434"))
    index = str(arguments.get("index", DEFAULT_INDEX))
    run_root = str(arguments.get("run_root", memory_path('heal-runs')))
    guard_pid_file = str(arguments.get("guard_pid_file", memory_path('resource.pid')))
    force = bool(arguments.get("force", False))
    semantic = bool(arguments.get("semantic", False))
    dsn = str(arguments.get("dsn", default_dsn()))

    from agent_memory_postgres import fetch_episodes as _fetch_episodes

    episodes = _fetch_episodes(
        dsn,
        [project_slug] if project_slug else None,
        limit,
    )

    import time as _time

    run_dir = (
        Path(run_root).expanduser()
        / _time.strftime("%Y%m%d-%H%M%S")
    )
    plan = heal_plan_actions(
        episodes=episodes,
        embedding_index_path=Path(index).expanduser().resolve(),
        model=model,
        ollama_url=ollama_url,
        run_dir=run_dir,
        guard_pid_file=Path(guard_pid_file),
        force=force,
        semantic=semantic,
    )
    return text_result(json_text(plan))


def tool_memory_heal_apply(arguments: dict[str, Any]) -> dict[str, object]:
    plan_path = str(arguments.get("plan", "")).strip()
    if not plan_path:
        raise McpError("plan is required")
    proposal_ids_raw = arguments.get("proposal_ids")
    if not isinstance(proposal_ids_raw, list) or not proposal_ids_raw:
        raise McpError(
            "proposal_ids must be a non-empty array — 'apply all' is intentionally refused"
        )
    proposal_ids = [str(pid) for pid in proposal_ids_raw]
    reviewed = arguments.get("reviewed") is True
    dsn = str(arguments.get("dsn", default_dsn()))
    write_postgres = bool(arguments.get("write_postgres", True))
    result = heal_apply_plan(
        plan_path=Path(plan_path).expanduser().resolve(),
        reviewed=reviewed,
        proposal_ids=proposal_ids,
        dsn=dsn,
        write_postgres=write_postgres,
    )
    return text_result(json_text(result))


def tool_doctrine_audit(arguments: dict[str, Any]) -> dict[str, object]:
    repos = arguments.get("repos") or ["."]
    if not isinstance(repos, list):
        raise McpError("repos must be an array")
    sessions_arg = arguments.get("sessions")
    if sessions_arg is not None and not isinstance(sessions_arg, list):
        raise McpError("sessions must be an array")
    try:
        since_days = int(arguments.get("since_days", 7))
    except (TypeError, ValueError):
        since_days = 7
    plan = run_doctrine_audit(
        repo_roots=[str(repo) for repo in repos],
        since_days=max(1, min(since_days, 365)),
        sessions=[str(session) for session in sessions_arg] if sessions_arg is not None else None,
        reviewed=arguments.get("reviewed") is True,
        dsn=str(arguments.get("dsn", default_dsn())),
        run_root=Path(str(arguments.get("run_root", memory_path('doctrine-audit-runs')))),
        baseline_file=str(arguments["baseline_file"]) if arguments.get("baseline_file") else None,
    )
    return text_result(json_text(plan))


def tool_memory_trends(arguments: dict[str, Any]) -> dict[str, object]:
    limit = clamp_limit(arguments.get("limit", 50), 50)
    try:
        episodes = fetch_episodes(
            str(arguments.get("dsn", default_dsn())),
            [AUDIT_PROJECT_SLUG],
            None,
        )
    except AgentMemoryPostgresError as exc:
        raise McpError(f"memory_trends requires Postgres audit ledger: {exc}") from exc
    finding_episodes = [
        episode
        for episode in episodes
        if episode.get("project_slug") == AUDIT_PROJECT_SLUG and not is_run_snapshot(episode)
    ]
    open_finding_episodes = [
        episode for episode in finding_episodes
        if audit_status(episode) == "open"
    ]
    import time

    if open_finding_episodes:
        latest_run = latest_audit_run_id(episodes)
        prior, current = global_finding_trend_windows(finding_episodes, latest_run)
        return text_result(
            render_trend_digest(
                prior=prior,
                current=current,
                as_of=time.strftime("%Y-%m-%d"),
                max_items=limit,
            )
        )
    snapshot_digest = latest_snapshot_digest(episodes)
    if snapshot_digest is not None:
        return text_result(snapshot_digest)
    if finding_episodes:
        return text_result(
            render_trend_digest(
                prior=[],
                current=finding_episodes,
                as_of=time.strftime("%Y-%m-%d"),
                max_items=limit,
            )
        )
    return text_result(render_trend_digest(prior=[], current=[], as_of=time.strftime("%Y-%m-%d"), max_items=limit))


def tool_project_truth(arguments: dict[str, Any]) -> dict[str, object]:
    project_root = resolve_project_root(arguments.get("project_path"))
    document = str(arguments.get("document", "summary"))
    max_chars = clamp_max_chars(arguments.get("max_chars"))

    if document == "summary":
        docs = {
            "identity": read_text_excerpt(project_root / PROJECT_TRUTH_FILES["identity"], max_chars, tail=False),
            "progress": read_text_excerpt(project_root / PROJECT_TRUTH_FILES["progress"], max_chars, tail=True),
            "agents": read_text_excerpt(project_root / PROJECT_TRUTH_FILES["agents"], max_chars, tail=False),
        }
        return text_result(
            json_text(
                {
                    "project_root": str(project_root),
                    "truth_layer": "identity.md + AGENTS.md + progress.md",
                    "documents": docs,
                }
            )
        )

    relative = PROJECT_TRUTH_FILES.get(document)
    if relative is None:
        raise McpError(
            "document must be one of: "
            + ", ".join(["summary", *sorted(PROJECT_TRUTH_FILES)])
        )
    tail = document == "progress"
    payload = read_text_excerpt(project_root / relative, max_chars, tail=tail)
    payload["document"] = document
    payload["project_root"] = str(project_root)
    return text_result(json_text(payload))


TOOLS = {
    "memory_search": {
        "description": "Search current canonical memory episodes and cross-project lessons. Falls back to the local SQLite index when Postgres is unavailable.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "project_slug": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        "handler": tool_memory_search,
    },
    "memory_recent": {
        "description": "Return recent structured memory episodes from the global memory ledger.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_slug": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
        },
        "handler": tool_memory_recent,
    },
    "memory_capture": {
        "description": "Append a structured durable memory episode. Do not include secrets or raw transcripts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "project_path": {"type": "string"},
                "project_slug": {"type": "string"},
                "scope": {"type": "string", "enum": ["global", "project", "session"]},
                "source": {"type": "string"},
                "decisions": {"type": "array", "items": {"type": "string"}},
                "supersedes": {"type": "array", "items": {"type": "string"}},
                "files_touched": {"type": "array", "items": {"type": "string"}},
                "commands": {"type": "array", "items": {"type": "string"}},
                "verification": {"type": "array", "items": {"type": "string"}},
                "open_questions": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "next_steps": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary"],
        },
        "handler": tool_memory_capture,
    },
    "memory_guard": {
        "description": "Check whether the configured resource-heavy guard process appears active before local memory model work.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "guard_pid_file": {"type": "string", "default": memory_path('resource.pid')},
                "force": {"type": "boolean", "default": False},
            },
        },
        "handler": tool_memory_guard,
    },
    "memory_stats": {
        "description": "Return local memory embedding index stats.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_memory_stats,
    },
    "memory_history_scan": {
        "description": "Inventory local Codex and Claude conversation history candidates without ingesting raw transcripts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "home": {"type": "string"},
                "source": {"type": "string", "enum": ["all", "codex", "codex-index", "claude"]},
                "project": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
        "handler": tool_history_scan,
    },
    "memory_retro_import": {
        "description": "Draft or explicitly capture a structured memory episode from one selected historical Codex or Claude session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": ["codex", "claude"]},
                "session_id": {"type": "string"},
                "project_filter": {"type": "string"},
                "project_path": {"type": "string"},
                "project_slug": {"type": "string"},
                "scope": {"type": "string", "enum": ["global", "project", "session"]},
                "home": {"type": "string"},
                "max_items": {"type": "integer", "default": 8},
                "excerpt_count": {"type": "integer", "default": 4},
                "capture": {"type": "boolean", "default": False},
                "reviewed": {"type": "boolean", "default": False},
                "ledger": {"type": "string"},
            },
            "required": ["source", "session_id"],
        },
        "handler": tool_retro_import,
    },
    "memory_dream_plan": {
        "description": "Create an audited dream consolidation plan for a project memory slug.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_slug": {"type": "string"},
                "project_path": {"type": "string"},
                "alias": {"type": "array", "items": {"type": "string"}},
                "alias_file": {"type": "string"},
                "source": {"type": "string", "enum": ["auto", "postgres", "jsonl"]},
                "ledger": {"type": "string"},
                "index": {"type": "string"},
                "dsn": {"type": "string"},
                "run_root": {"type": "string"},
                "promote_threshold": {"type": "integer", "default": 3},
            },
            "required": [],
        },
        "handler": tool_dream_plan,
    },
    "memory_dream_apply": {
        "description": "Apply reviewed reversible updates from a dream consolidation plan.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan": {"type": "string"},
                "reviewed": {"type": "boolean", "default": False},
                "dsn": {"type": "string"},
                "no_postgres": {"type": "boolean", "default": False},
            },
            "required": ["plan"],
        },
        "handler": tool_dream_apply,
    },
    "memory_doctrine_audit": {
        "description": (
            "Audit git/session activity for doctrine-gate violations. "
            "Findings are quarantined under _audit-doctrine; plan-only unless reviewed=true."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repos": {"type": "array", "items": {"type": "string"}},
                "since_days": {"type": "integer", "default": 7},
                "sessions": {"type": "array", "items": {"type": "string"}},
                "reviewed": {"type": "boolean", "default": False},
                "dsn": {"type": "string"},
                "run_root": {"type": "string"},
                "baseline_file": {"type": "string"},
            },
        },
        "handler": tool_doctrine_audit,
    },
    "memory_trends": {
        "description": "Return the doctrine-audit trend digest as terminal-readable markdown.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
                "dsn": {"type": "string"},
            },
        },
        "handler": tool_memory_trends,
    },
    "memory_heal_plan": {
        "description": (
            "Detect drift (contradictions + stale supersessions) across a project's memory "
            "corpus and produce a reviewable HEAL plan. Plan is written to run_dir; no "
            "Postgres mutations until memory_heal_apply is called with explicit proposal_ids."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_slug": {"type": "string"},
                "dsn": {"type": "string"},
                "index": {"type": "string"},
                "model": {"type": "string", "default": "nomic-embed-text"},
                "ollama_url": {"type": "string", "default": "http://127.0.0.1:11434"},
                "run_root": {"type": "string"},
                "guard_pid_file": {"type": "string", "default": memory_path('resource.pid')},
                "force": {"type": "boolean", "default": False},
                "semantic": {
                    "type": "boolean",
                    "default": False,
                    "description": "Enable the semantic contradiction detector (OFF by default; "
                                   "requires a fresh embedding index and a local embedding service).",
                },
                "limit": {"type": "integer"},
            },
        },
        "handler": tool_memory_heal_plan,
    },
    "memory_heal_apply": {
        "description": (
            "Apply a reviewed HEAL plan by writing supersedes[] edges to canonical episodes. "
            "Requires reviewed=true and an explicit non-empty proposal_ids list. "
            "'Apply all' is intentionally refused at every entry point."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan": {"type": "string"},
                "proposal_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit finding_ids to apply. Must be non-empty.",
                },
                "reviewed": {"type": "boolean", "default": False},
                "dsn": {"type": "string"},
                "write_postgres": {"type": "boolean", "default": True},
            },
            "required": ["plan", "proposal_ids"],
        },
        "handler": tool_memory_heal_apply,
    },
    "project_truth": {
        "description": "Read project doctrine and current progress files as the per-project truth layer, separate from conversation memory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string"},
                "document": {
                    "type": "string",
                    "enum": [
                        "summary",
                        "identity",
                        "agents",
                        "progress",
                        "anomalies",
                        "task",
                        "style",
                        "memory_doc",
                    ],
                    "default": "summary",
                },
                "max_chars": {"type": "integer", "default": DEFAULT_MAX_CHARS},
            },
        },
        "handler": tool_project_truth,
    },
}


def send(message: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def response(message_id: object, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def error_response(message_id: object, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def list_tools() -> dict[str, object]:
    return {
        "tools": [
            {
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["inputSchema"],
            }
            for name, spec in TOOLS.items()
        ]
    }


def handle(message: dict[str, Any]) -> dict[str, object] | None:
    message_id = message.get("id")
    method = message.get("method")
    if message_id is None and method and str(method).startswith("notifications/"):
        return None
    if method == "initialize":
        return response(
            message_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "tools/list":
        return response(message_id, list_tools())
    if method == "tools/call":
        params = message.get("params", {})
        if not isinstance(params, dict):
            return error_response(message_id, -32602, "params must be an object")
        name = str(params.get("name", ""))
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return error_response(message_id, -32602, "arguments must be an object")
        spec = TOOLS.get(name)
        if spec is None:
            return error_response(message_id, -32602, f"Unknown tool: {name}")
        try:
            return response(message_id, spec["handler"](arguments))
        except (
            McpError,
            AgentMemoryEpisodeError,
            AgentMemoryPostgresError,
            AgentMemoryRetroImportError,
            AgentMemoryDreamError,
            AgentMemoryDoctrineAuditError,
            AgentMemoryHealError,
            sqlite3.Error,
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as exc:
            return response(message_id, text_result(str(exc), is_error=True))
    return error_response(message_id, -32601, f"Method not found: {method}")


def serve() -> int:
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped)
        except json.JSONDecodeError as exc:
            send(error_response(None, -32700, f"Parse error: {exc}"))
            continue
        if not isinstance(message, dict):
            send(error_response(None, -32600, "Invalid request"))
            continue
        result = handle(message)
        if result is not None:
            send(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local MCP server for durable agent memory.")
    parser.set_defaults(func=lambda _args: serve())
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
