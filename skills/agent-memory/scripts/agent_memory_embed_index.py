#!/usr/bin/env python3
# Public edition adapted from the owner's Agent Memory server; see NOTICE.
from __future__ import annotations
from memory_paths import memory_path

import argparse
import json
import math
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from agent_memory_episode import AgentMemoryEpisodeError, validate_episode_payload
from agent_memory_resource_guard import (
    DEFAULT_GUARD_PID_FILE,
    ResourceGuardError,
    assert_guard_inactive,
)


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_LEDGER = os.environ.get("AGENT_MEMORY_LEDGER") or memory_path('episodes.jsonl')
DEFAULT_INDEX = os.environ.get("AGENT_MEMORY_INDEX") or memory_path('episode_embeddings.sqlite')
DEFAULT_KEEP_ALIVE = "24h"
DEFAULT_MAX_INPUT_CHARS = 6_000
TRUNCATION_MARKER = "\n[truncated for embedding]"


class AgentMemoryEmbedError(Exception):
    """Raised when the local embedding index cannot complete an operation."""


@dataclass(frozen=True, slots=True)
class IndexedEpisode:
    episode_id: str
    project_slug: str
    scope: str
    summary: str
    score: float
    tags: list[str]


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def read_jsonl(path: Path) -> list[dict[str, object]]:
    episodes: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise AgentMemoryEmbedError(
                    f"{path}:{line_number}: invalid JSON: {exc}"
                ) from exc
            try:
                validate_episode_payload(payload)
            except AgentMemoryEpisodeError as exc:
                raise AgentMemoryEmbedError(f"{path}:{line_number}: {exc}") from exc
            episodes.append(payload)
    return episodes


def clamp_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    prefix_limit = max(0, max_chars - len(TRUNCATION_MARKER))
    return value[:prefix_limit].rstrip() + TRUNCATION_MARKER


def episode_text(payload: dict[str, object], max_chars: int = DEFAULT_MAX_INPUT_CHARS) -> str:
    parts = [
        f"project: {payload['project_slug']}",
        f"scope: {payload['scope']}",
        f"summary: {payload['summary']}",
    ]
    for field in (
        "decisions",
        "supersedes",
        "files_touched",
        "commands",
        "verification",
        "open_questions",
        "risks",
        "next_steps",
        "tags",
    ):
        values = payload.get(field, [])
        if isinstance(values, list) and values:
            parts.append(f"{field}: " + "; ".join(str(value) for value in values))
    return clamp_text("\n".join(parts), max_chars)


def post_json(url: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AgentMemoryEmbedError(
            f"Ollama request failed: HTTP {exc.code}: {detail[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise AgentMemoryEmbedError(f"Ollama request failed: {exc}") from exc
    return json.loads(raw)


def ollama_embed(
    base_url: str,
    model: str,
    inputs: list[str],
    keep_alive: str,
    timeout: float,
) -> list[list[float]]:
    payload: dict[str, object] = {
        "model": model,
        "input": inputs,
        "keep_alive": keep_alive,
    }
    data = post_json(f"{base_url.rstrip('/')}/api/embed", payload, timeout)
    embeddings = data.get("embeddings")
    if not isinstance(embeddings, list):
        raise AgentMemoryEmbedError("Ollama response did not include embeddings")
    vectors: list[list[float]] = []
    for embedding in embeddings:
        if not isinstance(embedding, list):
            raise AgentMemoryEmbedError("Ollama returned a malformed embedding")
        vectors.append([float(value) for value in embedding])
    return vectors


def connect_index(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS episode_embeddings (
            episode_id TEXT PRIMARY KEY,
            project_slug TEXT NOT NULL,
            scope TEXT NOT NULL,
            summary TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            source_json TEXT NOT NULL,
            text TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            embedding_json TEXT NOT NULL,
            embedded_at_ms INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_episode_embeddings_project "
        "ON episode_embeddings(project_slug)"
    )
    return conn


def existing_ids(conn: sqlite3.Connection, model: str) -> set[str]:
    rows = conn.execute(
        "SELECT episode_id FROM episode_embeddings WHERE embedding_model = ?",
        (model,),
    ).fetchall()
    return {str(row[0]) for row in rows}


def chunks(values: list[dict[str, object]], size: int) -> list[list[dict[str, object]]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def load_index_episodes(args: argparse.Namespace) -> tuple[list[dict[str, object]], str]:
    source = str(args.source)
    if source == "postgres":
        from agent_memory_postgres import fetch_episodes

        return fetch_episodes(args.dsn, args.project_slug, args.limit), "postgres"

    ledger = Path(args.ledger).expanduser().resolve()
    episodes = read_jsonl(ledger)
    if args.project_slug:
        slugs = set(args.project_slug)
        episodes = [
            episode for episode in episodes
            if str(episode.get("project_slug", "")) in slugs
        ]
    if args.limit is not None:
        episodes = episodes[: args.limit]
    return episodes, "jsonl"


def index_episodes(args: argparse.Namespace) -> int:
    assert_guard_inactive(Path(args.guard_pid_file), args.force)
    index_path = Path(args.index).expanduser().resolve()
    episodes, source = load_index_episodes(args)
    conn = connect_index(index_path)
    seen = existing_ids(conn, args.model)
    pending = [
        episode for episode in episodes if str(episode["episode_id"]) not in seen or args.reindex
    ]

    total = 0
    for batch in chunks(pending, args.batch_size):
        texts = [episode_text(episode) for episode in batch]
        vectors = ollama_embed(
            base_url=args.ollama_url,
            model=args.model,
            inputs=texts,
            keep_alive=args.keep_alive,
            timeout=args.timeout,
        )
        if len(vectors) != len(batch):
            raise AgentMemoryEmbedError(
                f"Ollama returned {len(vectors)} vectors for {len(batch)} inputs"
            )
        now = now_ms()
        with conn:
            for episode, text, vector in zip(batch, texts, vectors, strict=True):
                tags = episode.get("tags", [])
                conn.execute(
                    """
                    INSERT OR REPLACE INTO episode_embeddings (
                        episode_id,
                        project_slug,
                        scope,
                        summary,
                        tags_json,
                        source_json,
                        text,
                        embedding_model,
                        dimensions,
                        embedding_json,
                        embedded_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(episode["episode_id"]),
                        str(episode["project_slug"]),
                        str(episode["scope"]),
                        str(episode["summary"]),
                        json.dumps(tags, sort_keys=True),
                        json.dumps(episode, sort_keys=True),
                        text,
                        args.model,
                        len(vector),
                        json.dumps(vector),
                        now,
                    ),
                )
                total += 1

    print(
        f"indexed episodes={total} source={source} "
        f"model={args.model} index={index_path}"
    )
    return 0


def dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def norm(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def cosine(left: list[float], right: list[float]) -> float:
    denominator = norm(left) * norm(right)
    if denominator == 0:
        return 0.0
    return dot(left, right) / denominator


def search(args: argparse.Namespace) -> int:
    if not args.allow_while_guarded:
        assert_guard_inactive(Path(args.guard_pid_file), args.force)
    index_path = Path(args.index).expanduser().resolve()
    conn = connect_index(index_path)
    query_vector = ollama_embed(
        base_url=args.ollama_url,
        model=args.model,
        inputs=[args.query],
        keep_alive=args.keep_alive,
        timeout=args.timeout,
    )[0]

    params: list[object] = [args.model]
    where = "embedding_model = ?"
    if args.project_slug:
        where += " AND project_slug = ?"
        params.append(args.project_slug)

    rows = conn.execute(
        f"""
        SELECT episode_id, project_slug, scope, summary, tags_json, embedding_json
        FROM episode_embeddings
        WHERE {where}
        """,
        params,
    ).fetchall()
    results: list[IndexedEpisode] = []
    for row in rows:
        vector = [float(value) for value in json.loads(row[5])]
        if len(vector) != len(query_vector):
            continue
        tags = json.loads(row[4])
        results.append(
            IndexedEpisode(
                episode_id=str(row[0]),
                project_slug=str(row[1]),
                scope=str(row[2]),
                summary=str(row[3]),
                score=cosine(query_vector, vector),
                tags=[str(tag) for tag in tags],
            )
        )
    results.sort(key=lambda result: result.score, reverse=True)
    for result in results[: args.limit]:
        print(
            f"{result.score:.4f} {result.project_slug} {result.episode_id} "
            f"{result.summary} tags={','.join(result.tags)}"
        )
    return 0


def warm(args: argparse.Namespace) -> int:
    assert_guard_inactive(Path(args.guard_pid_file), args.force)
    vectors = ollama_embed(
        base_url=args.ollama_url,
        model=args.model,
        inputs=["agent memory warmup"],
        keep_alive=args.keep_alive,
        timeout=args.timeout,
    )
    print(
        f"warm model={args.model} dimensions={len(vectors[0])} keep_alive={args.keep_alive}"
    )
    return 0


def stats(args: argparse.Namespace) -> int:
    index_path = Path(args.index).expanduser().resolve()
    conn = connect_index(index_path)
    rows = conn.execute(
        """
        SELECT project_slug, embedding_model, COUNT(*), MAX(embedded_at_ms)
        FROM episode_embeddings
        GROUP BY project_slug, embedding_model
        ORDER BY project_slug, embedding_model
        """
    ).fetchall()
    if not rows:
        print(f"index={index_path} episodes=0")
        return 0
    for project_slug, model, count, embedded_at_ms in rows:
        print(
            f"project={project_slug} model={model} episodes={count} "
            f"latest_embedded_at_ms={embedded_at_ms}"
        )
    return 0


def guard(args: argparse.Namespace) -> int:
    status = assert_guard_inactive(Path(args.guard_pid_file), args.force)
    if status.active:
        print(f"guard_active=1 detail={status.detail}")
    else:
        print(f"guard_active=0 detail={status.detail}")
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--keep-alive", default=DEFAULT_KEEP_ALIVE)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--guard-pid-file", default=str(DEFAULT_GUARD_PID_FILE))
    parser.add_argument("--force", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local Ollama embedding index for structured agent memory episodes."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    warm_parser = subparsers.add_parser("warm")
    add_common_args(warm_parser)
    warm_parser.set_defaults(func=warm)

    index_parser = subparsers.add_parser("index")
    add_common_args(index_parser)
    index_parser.add_argument("--source", choices=["jsonl", "postgres"], default="jsonl")
    index_parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    index_parser.add_argument(
        "--dsn",
        default=os.environ.get("AGENT_MEMORY_POSTGRES_DSN", "postgresql:///forge_agent_memory"),
    )
    index_parser.add_argument("--index", default=DEFAULT_INDEX)
    index_parser.add_argument("--project-slug", action="append")
    index_parser.add_argument("--batch-size", type=int, default=16)
    index_parser.add_argument("--limit", type=int)
    index_parser.add_argument("--reindex", action="store_true")
    index_parser.set_defaults(func=index_episodes)

    search_parser = subparsers.add_parser("search")
    add_common_args(search_parser)
    search_parser.add_argument("query")
    search_parser.add_argument("--index", default=DEFAULT_INDEX)
    search_parser.add_argument("--project-slug")
    search_parser.add_argument("--limit", type=int, default=5)
    search_parser.add_argument(
        "--allow-while-guarded",
        action="store_true",
        help="Allow query embedding while the guarded resource process appears active.",
    )
    search_parser.set_defaults(func=search)

    stats_parser = subparsers.add_parser("stats")
    stats_parser.add_argument("--index", default=DEFAULT_INDEX)
    stats_parser.set_defaults(func=stats)

    guard_parser = subparsers.add_parser("guard")
    guard_parser.add_argument("--guard-pid-file", default=str(DEFAULT_GUARD_PID_FILE))
    guard_parser.add_argument("--force", action="store_true")
    guard_parser.set_defaults(func=guard)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (AgentMemoryEmbedError, ResourceGuardError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
