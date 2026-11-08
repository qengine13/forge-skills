#!/usr/bin/env python3
# Public edition adapted from the owner's Agent Memory server; see NOTICE.
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent_memory_embed_index import DEFAULT_LEDGER, episode_text, read_jsonl


DEFAULT_DSN = "postgresql:///forge_agent_memory"
DEFAULT_MAINTENANCE_DSN = "postgresql:///postgres"
DEFAULT_DATABASE = "forge_agent_memory"
DEFAULT_SCHEMA = "agent_memory"


class AgentMemoryPostgresError(Exception):
    """Raised when the Postgres memory store cannot complete an operation."""


def default_dsn() -> str:
    if os.environ.get("FORGE_MEMORY_MANAGED") == "1" and not os.environ.get("AGENT_MEMORY_POSTGRES_DSN"):
        raise AgentMemoryPostgresError("This advanced operation requires an explicitly configured --postgres-dsn")
    return os.environ.get("AGENT_MEMORY_POSTGRES_DSN", DEFAULT_DSN)


def require_psql() -> str:
    psql = shutil.which("psql")
    if psql is None:
        raise AgentMemoryPostgresError("psql is not available on PATH")
    return psql


def require_createdb() -> str:
    createdb = shutil.which("createdb")
    if createdb is None:
        raise AgentMemoryPostgresError("createdb is not available on PATH")
    return createdb


def run_psql(
    dsn: str,
    sql: str,
    *,
    variables: dict[str, object] | None = None,
    capture: bool = False,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        require_psql(),
        dsn,
        "--no-psqlrc",
        "--quiet",
        "--set",
        "ON_ERROR_STOP=1",
    ]
    for key, value in (variables or {}).items():
        command.extend(["--set", f"{key}={value}"])
    if capture:
        command.extend(["--tuples-only", "--no-align"])
    try:
        return subprocess.run(
            command,
            input=sql,
            text=True,
            capture_output=capture,
            check=True,
            timeout=timeout_seconds,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise AgentMemoryPostgresError(detail) from exc
    except subprocess.TimeoutExpired as exc:
        raise AgentMemoryPostgresError(
            f"psql timed out after {timeout_seconds} seconds"
        ) from exc


SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS agent_memory;

CREATE TABLE IF NOT EXISTS agent_memory.projects (
    project_slug text PRIMARY KEY,
    project_path text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_memory.episodes (
    episode_id text PRIMARY KEY,
    schema_version text NOT NULL,
    created_at_ms bigint NOT NULL,
    scope text NOT NULL CHECK (scope IN ('global', 'project', 'session')),
    project_slug text NOT NULL REFERENCES agent_memory.projects(project_slug),
    project_path text NOT NULL,
    source text NOT NULL,
    summary text NOT NULL,
    text text NOT NULL,
    payload jsonb NOT NULL,
    decisions jsonb NOT NULL DEFAULT '[]'::jsonb,
    supersedes jsonb NOT NULL DEFAULT '[]'::jsonb,
    files_touched jsonb NOT NULL DEFAULT '[]'::jsonb,
    commands jsonb NOT NULL DEFAULT '[]'::jsonb,
    verification jsonb NOT NULL DEFAULT '[]'::jsonb,
    open_questions jsonb NOT NULL DEFAULT '[]'::jsonb,
    risks jsonb NOT NULL DEFAULT '[]'::jsonb,
    next_steps jsonb NOT NULL DEFAULT '[]'::jsonb,
    tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    inserted_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_episodes_project_created
    ON agent_memory.episodes(project_slug, created_at_ms DESC);

CREATE INDEX IF NOT EXISTS idx_agent_memory_episodes_tags
    ON agent_memory.episodes USING gin(tags);

CREATE INDEX IF NOT EXISTS idx_agent_memory_episodes_payload
    ON agent_memory.episodes USING gin(payload);
"""


UPSERT_SQL = """
WITH input AS (
    SELECT :'payload_json'::jsonb AS payload,
           :'episode_text'::text AS text
),
project_upsert AS (
    INSERT INTO agent_memory.projects (
        project_slug,
        project_path,
        first_seen_at,
        last_seen_at
    )
    SELECT payload->>'project_slug',
           payload->>'project_path',
           now(),
           now()
    FROM input
    ON CONFLICT (project_slug) DO UPDATE SET
        project_path = EXCLUDED.project_path,
        last_seen_at = now()
    RETURNING project_slug
)
INSERT INTO agent_memory.episodes (
    episode_id,
    schema_version,
    created_at_ms,
    scope,
    project_slug,
    project_path,
    source,
    summary,
    text,
    payload,
    decisions,
    supersedes,
    files_touched,
    commands,
    verification,
    open_questions,
    risks,
    next_steps,
    tags,
    updated_at
)
SELECT payload->>'episode_id',
       payload->>'schema_version',
       (payload->>'created_at_ms')::bigint,
       payload->>'scope',
       payload->>'project_slug',
       payload->>'project_path',
       payload->>'source',
       payload->>'summary',
       text,
       payload,
       COALESCE(payload->'decisions', '[]'::jsonb),
       COALESCE(payload->'supersedes', '[]'::jsonb),
       COALESCE(payload->'files_touched', '[]'::jsonb),
       COALESCE(payload->'commands', '[]'::jsonb),
       COALESCE(payload->'verification', '[]'::jsonb),
       COALESCE(payload->'open_questions', '[]'::jsonb),
       COALESCE(payload->'risks', '[]'::jsonb),
       COALESCE(payload->'next_steps', '[]'::jsonb),
       COALESCE(payload->'tags', '[]'::jsonb),
       now()
FROM input
JOIN project_upsert
  ON project_upsert.project_slug = input.payload->>'project_slug'
ON CONFLICT (episode_id) DO UPDATE SET
    schema_version = EXCLUDED.schema_version,
    created_at_ms = EXCLUDED.created_at_ms,
    scope = EXCLUDED.scope,
    project_slug = EXCLUDED.project_slug,
    project_path = EXCLUDED.project_path,
    source = EXCLUDED.source,
    summary = EXCLUDED.summary,
    text = EXCLUDED.text,
    payload = EXCLUDED.payload,
    decisions = EXCLUDED.decisions,
    supersedes = EXCLUDED.supersedes,
    files_touched = EXCLUDED.files_touched,
    commands = EXCLUDED.commands,
    verification = EXCLUDED.verification,
    open_questions = EXCLUDED.open_questions,
    risks = EXCLUDED.risks,
    next_steps = EXCLUDED.next_steps,
    tags = EXCLUDED.tags,
    updated_at = now();
"""


def compact_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def lessons_schema_sql() -> str:
    """DDL for the cross-project lessons table.

    Read from lessons_schema.sql rather than inlined here so the table is
    defined in exactly one place: an init path carrying its own copy would drift
    from the recovery path the moment either changed.
    """
    path = Path(__file__).resolve().parent / "lessons_schema.sql"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def init_all_schemas(dsn: str) -> None:
    """Initialize the episode and lesson tables in the selected database."""
    run_psql(dsn, SCHEMA_SQL)
    lessons = lessons_schema_sql()
    if lessons.strip():
        run_psql(dsn, lessons)


def init_schema(args: argparse.Namespace) -> int:
    init_all_schemas(args.dsn)
    print(f"initialized schema={DEFAULT_SCHEMA} dsn={args.dsn}")
    return 0


def create_database(args: argparse.Namespace) -> int:
    sql = "SELECT 1 FROM pg_database WHERE datname = :'database';"
    result = run_psql(
        args.maintenance_dsn,
        sql,
        variables={"database": args.database},
        capture=True,
    )
    if result.stdout.strip() == "1":
        print(f"database_exists database={args.database}")
        return 0
    try:
        subprocess.run(
            [require_createdb(), args.database],
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise AgentMemoryPostgresError(detail) from exc
    print(f"created database={args.database}")
    return 0


def upsert_episode(dsn: str, payload: dict[str, object]) -> None:
    run_psql(
        dsn,
        UPSERT_SQL,
        variables={
            "payload_json": compact_json(payload),
            "episode_text": episode_text(payload),
        },
    )


def fetch_episode_by_id(dsn: str, episode_id: str) -> dict[str, object] | None:
    """Return the canonical episode payload for *episode_id*, or None if not found.

    Used by ``apply_heal_plan`` to detect snapshot drift: if the canonical
    episode in Postgres differs from the snapshot payload captured at plan time,
    the supersession edge is refused rather than silently overwriting intervening
    changes made by other systems.
    """
    sql = """
SELECT payload::text
FROM agent_memory.episodes
WHERE episode_id = :'episode_id'
LIMIT 1;
"""
    try:
        result = run_psql(
            dsn,
            sql,
            variables={"episode_id": episode_id},
            capture=True,
        )
    except AgentMemoryPostgresError:
        return None
    text = result.stdout.strip()
    if not text:
        return None
    return json.loads(text)  # type: ignore[no-any-return]


def sync_jsonl(args: argparse.Namespace) -> int:
    if args.init:
        init_all_schemas(args.dsn)
    path = Path(args.path).expanduser().resolve()
    episodes = read_jsonl(path)
    requested_ids = set(getattr(args, "episode_id", []) or [])
    if requested_ids:
        available_ids = {str(payload["episode_id"]) for payload in episodes}
        missing_ids = requested_ids - available_ids
        if missing_ids:
            raise AgentMemoryPostgresError(
                "requested episode_id not found in JSONL: "
                + ", ".join(sorted(missing_ids))
            )
        episodes = [
            payload
            for payload in episodes
            if str(payload["episode_id"]) in requested_ids
        ]
    if args.limit is not None:
        episodes = episodes[: args.limit]
    synced = 0
    skipped_existing = 0
    for payload in episodes:
        if getattr(args, "missing_only", False) and fetch_episode_by_id(
            args.dsn,
            str(payload["episode_id"]),
        ):
            skipped_existing += 1
            continue
        upsert_episode(args.dsn, payload)
        synced += 1
    print(
        f"synced episodes={synced} skipped_existing={skipped_existing} "
        f"dsn={args.dsn} source={path}"
    )
    return 0


def status(args: argparse.Namespace) -> int:
    sql = """
SELECT jsonb_build_object(
    'connected', true,
    'server_version', current_setting('server_version'),
    'schema_exists', EXISTS (
        SELECT 1 FROM information_schema.schemata
        WHERE schema_name = 'agent_memory'
    ),
    'episodes_table_exists', to_regclass('agent_memory.episodes') IS NOT NULL
)::text;
"""
    try:
        result = run_psql(args.dsn, sql, capture=True)
    except AgentMemoryPostgresError as exc:
        print(
            json.dumps(
                {"connected": False, "dsn": args.dsn, "error": str(exc)},
                sort_keys=True,
            )
        )
        return 1
    print(result.stdout.strip())
    return 0


def stats(args: argparse.Namespace) -> int:
    print(compact_json(fetch_stats(args.dsn)))
    return 0


def fetch_stats(dsn: str) -> list[dict[str, object]]:
    sql = """
SELECT COALESCE(jsonb_agg(row_to_json(rows)::jsonb), '[]'::jsonb)::text
FROM (
    SELECT project_slug,
           COUNT(*)::int AS episodes,
           MAX(created_at_ms) AS latest_created_at_ms
    FROM agent_memory.episodes
    GROUP BY project_slug
    ORDER BY project_slug
) rows;
"""
    result = run_psql(dsn, sql, capture=True)
    return json.loads(result.stdout.strip() or "[]")


def recent(args: argparse.Namespace) -> int:
    print(compact_json(fetch_recent(args.dsn, args.project_slug, args.limit)))
    return 0


def list_episodes(args: argparse.Namespace) -> int:
    print(compact_json(fetch_episodes(args.dsn, args.project_slug, args.limit)))
    return 0


def fetch_recent(
    dsn: str,
    project_slug: str | None,
    limit: int,
) -> list[dict[str, object]]:
    where = "TRUE"
    variables: dict[str, object] = {"limit": limit}
    if project_slug:
        where = "project_slug = :'project_slug'"
        variables["project_slug"] = project_slug
    sql = f"""
SELECT COALESCE(jsonb_agg(payload ORDER BY created_at_ms DESC), '[]'::jsonb)::text
FROM (
    SELECT payload, created_at_ms
    FROM agent_memory.episodes
    WHERE {where}
    ORDER BY created_at_ms DESC
    LIMIT :limit
) rows;
"""
    result = run_psql(dsn, sql, variables=variables, capture=True)
    return json.loads(result.stdout.strip() or "[]")


def fetch_recent_excluding(
    dsn: str,
    excluded_project_slug: str,
    limit: int,
) -> list[dict[str, object]]:
    variables: dict[str, object] = {
        "excluded_project_slug": excluded_project_slug,
        "limit": limit,
    }
    sql = """
SELECT COALESCE(jsonb_agg(payload ORDER BY created_at_ms DESC), '[]'::jsonb)::text
FROM (
    SELECT payload, created_at_ms
    FROM agent_memory.episodes
    WHERE project_slug != :'excluded_project_slug'
    ORDER BY created_at_ms DESC
    LIMIT :limit
) rows;
"""
    result = run_psql(dsn, sql, variables=variables, capture=True)
    return json.loads(result.stdout.strip() or "[]")


def fetch_canonical_recent(
    dsn: str,
    project_slug: str,
    limit: int,
    *,
    timeout_seconds: float | None = None,
) -> list[dict[str, object]]:
    """Fetch newest episodes after filtering superseded rows in PostgreSQL.

    Example:
        fetch_canonical_recent(
            "postgresql:///forge_agent_memory",
            "sample-project",
            5,
            timeout_seconds=3,
        )
    """
    variables: dict[str, object] = {
        "project_slug": project_slug,
        "limit": limit,
    }
    sql = """
WITH superseded AS (
    SELECT DISTINCT older_id AS episode_id
    FROM agent_memory.episodes AS episode
    CROSS JOIN LATERAL
        jsonb_array_elements_text(
            CASE
                WHEN jsonb_typeof(supersedes) = 'array' THEN supersedes
                ELSE '[]'::jsonb
            END
        ) AS edges(older_id)
    WHERE episode.project_slug = :'project_slug'
      AND episode.scope <> 'session'
),
current_rows AS (
    SELECT episode.payload, episode.created_at_ms, episode.episode_id
    FROM agent_memory.episodes AS episode
    WHERE episode.project_slug = :'project_slug'
      AND episode.scope <> 'session'
      AND NOT EXISTS (
          SELECT 1
          FROM superseded
          WHERE superseded.episode_id = episode.episode_id
      )
    ORDER BY episode.created_at_ms DESC, episode.episode_id
    LIMIT :limit
)
SELECT COALESCE(
    jsonb_agg(payload ORDER BY created_at_ms DESC, episode_id),
    '[]'::jsonb
)::text
FROM current_rows;
"""
    result = run_psql(
        dsn,
        sql,
        variables=variables,
        capture=True,
        timeout_seconds=timeout_seconds,
    )
    return json.loads(result.stdout.strip() or "[]")


def fetch_episodes(
    dsn: str,
    project_slugs: list[str] | None,
    limit: int | None = None,
    *,
    timeout_seconds: float | None = None,
) -> list[dict[str, object]]:
    variables: dict[str, object] = {
        "project_slugs": compact_json(project_slugs or []),
        "limit": limit or 0,
    }
    sql = """
WITH selected AS (
    SELECT value AS project_slug
    FROM jsonb_array_elements_text(:'project_slugs'::jsonb)
),
rows AS (
    SELECT payload, created_at_ms
    FROM agent_memory.episodes
    WHERE (
        NOT EXISTS (SELECT 1 FROM selected)
        OR project_slug IN (SELECT project_slug FROM selected)
    )
    ORDER BY created_at_ms DESC
)
SELECT COALESCE(jsonb_agg(payload ORDER BY created_at_ms DESC), '[]'::jsonb)::text
FROM (
    SELECT payload, created_at_ms
    FROM rows
    WHERE (:limit)::int = 0 OR TRUE
    LIMIT CASE WHEN (:limit)::int = 0 THEN NULL ELSE (:limit)::int END
) limited;
"""
    result = run_psql(
        dsn,
        sql,
        variables=variables,
        capture=True,
        timeout_seconds=timeout_seconds,
    )
    return json.loads(result.stdout.strip() or "[]")


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dsn",
        default=default_dsn(),
        help="Postgres DSN. Defaults to AGENT_MEMORY_POSTGRES_DSN or postgresql:///forge_agent_memory.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Postgres durable store for structured agent memory episodes."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    add_common_args(init_parser)
    init_parser.set_defaults(func=init_schema)

    create_parser = subparsers.add_parser("create-db")
    create_parser.add_argument("--database", default=DEFAULT_DATABASE)
    create_parser.add_argument(
        "--maintenance-dsn",
        default=os.environ.get(
            "AGENT_MEMORY_POSTGRES_MAINTENANCE_DSN",
            DEFAULT_MAINTENANCE_DSN,
        ),
    )
    create_parser.set_defaults(func=create_database)

    sync_parser = subparsers.add_parser("sync-jsonl")
    add_common_args(sync_parser)
    sync_parser.add_argument("path", nargs="?", default=DEFAULT_LEDGER)
    sync_parser.add_argument("--limit", type=int)
    sync_parser.add_argument("--init", action="store_true")
    sync_parser.add_argument(
        "--episode-id",
        action="append",
        help="Import only this explicit episode ID. Repeat for multiple IDs.",
    )
    sync_parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Insert missing episodes without overwriting canonical rows.",
    )
    sync_parser.set_defaults(func=sync_jsonl)

    status_parser = subparsers.add_parser("status")
    add_common_args(status_parser)
    status_parser.set_defaults(func=status)

    stats_parser = subparsers.add_parser("stats")
    add_common_args(stats_parser)
    stats_parser.set_defaults(func=stats)

    recent_parser = subparsers.add_parser("recent")
    add_common_args(recent_parser)
    recent_parser.add_argument("--project-slug")
    recent_parser.add_argument("--limit", type=int, default=10)
    recent_parser.set_defaults(func=recent)

    list_parser = subparsers.add_parser("list")
    add_common_args(list_parser)
    list_parser.add_argument("--project-slug", action="append")
    list_parser.add_argument("--limit", type=int)
    list_parser.set_defaults(func=list_episodes)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AgentMemoryPostgresError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
