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
import time
from collections import defaultdict
from pathlib import Path

from agent_memory_embed_index import DEFAULT_INDEX, DEFAULT_LEDGER, read_jsonl
from agent_memory_episode import normalize_slug, validate_episode_payload
from agent_memory_postgres import (
    AgentMemoryPostgresError,
    default_dsn,
    fetch_episodes,
    upsert_episode,
)


DREAM_SCHEMA_VERSION = "agent-memory-dream-plan.v1"
DEFAULT_RUN_ROOT = Path(memory_path('dream-runs'))
ARCHIVE_TAG = "dream-archived"
MERGED_TAG = "dream-slug-merged"
CONSOLIDATED_TAG = "dream-consolidated"
MAX_EXAMPLE_SUMMARIES = 5


class AgentMemoryDreamError(Exception):
    """Raised when a memory dream plan cannot be produced or applied."""


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def compact_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(compact_json(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def parse_alias(values: list[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise AgentMemoryDreamError(f"alias must be FROM=TO: {value}")
        source, target = value.split("=", 1)
        source = source.strip()
        target = target.strip()
        if not source or not target:
            raise AgentMemoryDreamError(f"alias must be FROM=TO: {value}")
        aliases[source] = target
    return aliases


def load_alias_file(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    resolved = Path(path).expanduser().resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    aliases: dict[str, str] = {}
    if isinstance(payload, dict) and isinstance(payload.get("aliases"), dict):
        for source, target in payload["aliases"].items():
            aliases[str(source)] = str(target)
        return aliases
    if isinstance(payload, dict) and isinstance(payload.get("canonical"), dict):
        for target, config in payload["canonical"].items():
            if not isinstance(config, dict):
                continue
            for source in config.get("aliases", []):
                aliases[str(source)] = str(target)
        return aliases
    raise AgentMemoryDreamError(
        f"{resolved} must contain {{\"aliases\":{{...}}}} or {{\"canonical\":{{...}}}}"
    )


def resolved_slug(slug: str, aliases: dict[str, str]) -> str:
    seen: set[str] = set()
    current = slug
    while current in aliases:
        if current in seen:
            raise AgentMemoryDreamError(f"alias cycle detected at {current}")
        seen.add(current)
        current = aliases[current]
    return current


def normalize_summary(summary: object) -> str:
    text = str(summary).lower()
    text = re.sub(r"session[:\s]+[a-f0-9-]{8,}", "session:<id>", text)
    text = re.sub(r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b", "<date>", text)
    text = re.sub(r"\b[0-9a-f]{7,40}\b", "<hash>", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def episode_tags(episode: dict[str, object]) -> list[str]:
    tags = episode.get("tags", [])
    if not isinstance(tags, list):
        return []
    return [str(tag) for tag in tags]


# Tag prefixes that carry operational/session metadata rather than topical signal.
# Exclude these from topical overlap: merged episodes share maintenance tags
# by construction, regardless of whether their subjects are related.
_META_TAG_PREFIXES = ("session:", "path:", "previous-slug:", "duplicate-of:")
_META_TAG_EXACT = {"dream-consolidated", "dream-slug-merged", "dream-archived"}


def meaningful_tags(episode: dict[str, object]) -> list[str]:
    return [
        tag for tag in episode_tags(episode)
        if tag not in _META_TAG_EXACT and not any(tag.startswith(p) for p in _META_TAG_PREFIXES)
    ]


def add_tags(episode: dict[str, object], tags: list[str]) -> dict[str, object]:
    updated = dict(episode)
    existing = episode_tags(updated)
    for tag in tags:
        if tag not in existing:
            existing.append(tag)
    updated["tags"] = existing
    validate_episode_payload(updated)
    return updated


def set_slug(episode: dict[str, object], project_slug: str) -> dict[str, object]:
    updated = dict(episode)
    updated["project_slug"] = project_slug
    updated = add_tags(
        updated,
        [
            MERGED_TAG,
            CONSOLIDATED_TAG,
            f"previous-slug:{episode.get('project_slug')}",
        ],
    )
    validate_episode_payload(updated)
    return updated


def archive_duplicate(episode: dict[str, object], kept_episode_id: str) -> dict[str, object]:
    updated = add_tags(
        dict(episode),
        [
            ARCHIVE_TAG,
            CONSOLIDATED_TAG,
            f"duplicate-of:{kept_episode_id}",
        ],
    )
    validate_episode_payload(updated)
    return updated


def read_jsonl_episodes(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return read_jsonl(path)


def postgres_enabled() -> bool:
    flag = os.environ.get("AGENT_MEMORY_USE_POSTGRES", "").strip().lower()
    return bool(os.environ.get("AGENT_MEMORY_POSTGRES_DSN")) or flag in {
        "1",
        "true",
        "yes",
        "on",
    }


def load_episodes(
    source: str,
    ledger: Path,
    dsn: str,
    slugs: list[str],
) -> tuple[list[dict[str, object]], str]:
    if source == "postgres" or (source == "auto" and postgres_enabled()):
        try:
            return fetch_episodes(dsn, slugs, None), "postgres"
        except AgentMemoryPostgresError:
            if source == "postgres":
                raise
    episodes = [
        episode for episode in read_jsonl_episodes(ledger)
        if not slugs or str(episode.get("project_slug")) in slugs
    ]
    return episodes, "jsonl"


def index_episode_ids(index: Path, project_slug: str) -> set[str]:
    if not index.exists():
        return set()
    uri = f"file:{index}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, timeout=10.0, uri=True)
    rows = conn.execute(
        "SELECT episode_id FROM episode_embeddings WHERE project_slug = ?",
        (project_slug,),
    ).fetchall()
    return {str(row[0]) for row in rows}


def plan_slug_merges(
    episodes: list[dict[str, object]],
    project_slug: str,
    aliases: dict[str, str],
    project_path: Path | None,
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    for episode in episodes:
        current = str(episode.get("project_slug", ""))
        target = resolved_slug(current, aliases)
        exact_path_match = (
            project_path is not None
            and Path(str(episode.get("project_path", ""))).expanduser().resolve() == project_path
        )
        if current == project_slug:
            continue
        if target != project_slug and not exact_path_match:
            continue
        reason = "alias" if target == project_slug else "exact_project_path"
        actions.append(
            {
                "kind": "merge_slug",
                "episode_id": episode["episode_id"],
                "from_slug": current,
                "to_slug": project_slug,
                "reason": reason,
                "before": episode,
                "after": set_slug(episode, project_slug),
            }
        )
    return actions


def plan_exact_duplicates(episodes: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for episode in episodes:
        if ARCHIVE_TAG in episode_tags(episode):
            continue
        groups[normalize_summary(episode.get("summary", ""))].append(episode)

    actions: list[dict[str, object]] = []
    for normalized, group in groups.items():
        if not normalized or len(group) < 2:
            continue
        ordered = sorted(group, key=lambda item: int(item.get("created_at_ms", 0)), reverse=True)
        kept = ordered[0]
        for duplicate in ordered[1:]:
            actions.append(
                {
                    "kind": "archive_duplicate",
                    "episode_id": duplicate["episode_id"],
                    "kept_episode_id": kept["episode_id"],
                    "match_key": normalized,
                    "before": duplicate,
                    "after": archive_duplicate(duplicate, str(kept["episode_id"])),
                }
            )
    return actions


def plan_pattern_promotions(
    episodes: list[dict[str, object]],
    threshold: int,
) -> list[dict[str, object]]:
    # Meta-tag guard: a tag on >25% of project episodes is a cross-cutting category
    # rather than a recurring pattern eligible for promotion.
    # If the corpus is too small to support both a min-pattern threshold AND the 25% cap
    # without conflict, skip promotions entirely — small corpora don't have enough signal.
    meta_tag_ceiling = int(len(episodes) * 0.25)
    if meta_tag_ceiling < threshold:
        return []

    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for episode in episodes:
        for tag in sorted(meaningful_tags(episode)):
            groups[tag].append(episode)

    actions: list[dict[str, object]] = []
    for tag, group in sorted(groups.items()):
        unique_ids = {str(episode["episode_id"]) for episode in group}
        if len(unique_ids) < threshold:
            continue
        if len(unique_ids) > meta_tag_ceiling:
            continue
        ordered = sorted(group, key=lambda item: int(item.get("created_at_ms", 0)), reverse=True)
        actions.append(
            {
                "kind": "propose_promotion",
                "tag": tag,
                "episode_ids": [str(episode["episode_id"]) for episode in ordered],
                "summary": f"Recurring memory pattern for tag '{tag}' across {len(unique_ids)} episodes.",
                "examples": [
                    str(episode.get("summary", ""))[:240]
                    for episode in ordered[:MAX_EXAMPLE_SUMMARIES]
                ],
            }
        )
    return actions


def plan_contradictions(episodes: list[dict[str, object]]) -> list[dict[str, object]]:
    """Delegates to agent_memory_heal.detect_lexical_contradictions.

    Detection/proposal logic lives in scripts/agent_memory_heal.py: dream is the
    orchestration shell and HEAL owns detection. Kept as a thin shim so existing
    call sites and fixtures continue to work without API churn.
    """
    from agent_memory_heal import detect_lexical_contradictions

    return detect_lexical_contradictions(episodes)


def plan_reindex(
    episodes: list[dict[str, object]],
    project_slug: str,
    index: Path,
    backend: str,
) -> list[dict[str, object]]:
    indexed = index_episode_ids(index, project_slug)
    missing = [
        str(episode["episode_id"]) for episode in episodes
        if str(episode.get("project_slug")) == project_slug
        and str(episode["episode_id"]) not in indexed
    ]
    if not missing:
        return []
    return [
        {
            "kind": "reindex_required",
            "project_slug": project_slug,
            "missing_count": len(missing),
            "missing_episode_ids": missing[:50],
            "command": (
                "python3 scripts/agent_memory_embed_index.py index "
                f"--source {backend} --project-slug {project_slug} "
                f"--reindex --index {index}"
            ),
        }
    ]


def build_plan(args: argparse.Namespace) -> dict[str, object]:
    aliases = {**load_alias_file(args.alias_file), **parse_alias(args.alias)}
    project_path = Path(args.project_path).expanduser().resolve() if args.project_path else None
    project_slug = args.project_slug or (normalize_slug(project_path) if project_path else "")
    if not project_slug:
        raise AgentMemoryDreamError("project_slug or project_path is required")
    candidate_slugs = [] if project_path else sorted({project_slug, *aliases.keys()})
    ledger = Path(args.ledger).expanduser().resolve()
    index = Path(args.index).expanduser().resolve()
    episodes, backend = load_episodes(args.source, ledger, args.dsn, candidate_slugs)
    target_episodes = [
        episode for episode in episodes
        if resolved_slug(str(episode.get("project_slug", "")), aliases) == project_slug
        or (
            project_path is not None
            and Path(str(episode.get("project_path", ""))).expanduser().resolve() == project_path
        )
    ]
    run = run_id()
    run_dir = Path(args.run_root).expanduser().resolve() / run

    actions: list[dict[str, object]] = []
    actions.extend(plan_slug_merges(target_episodes, project_slug, aliases, project_path))
    projected = [
        action["after"] if action["kind"] == "merge_slug" else episode
        for episode in target_episodes
        for action in [next(
            (
                item for item in actions
                if item["kind"] == "merge_slug" and item["episode_id"] == episode["episode_id"]
            ),
            {"kind": "none"},
        )]
    ]
    actions.extend(plan_exact_duplicates(projected))
    actions.extend(plan_pattern_promotions(projected, args.promote_threshold))
    actions.extend(plan_contradictions(projected))
    actions.extend(plan_reindex(projected, project_slug, index, backend))

    summary: dict[str, object] = {
        "backend": backend,
        "candidate_slugs": candidate_slugs or ["<all:project-path-scan>"],
        "project_path": str(project_path) if project_path else "",
        "target_episode_count": len(target_episodes),
        "actions": defaultdict(int),
        "applyable_actions": 0,
    }
    for action in actions:
        summary["actions"][str(action["kind"])] += 1
        if action["kind"] in {"merge_slug", "archive_duplicate"}:
            summary["applyable_actions"] += 1
    summary["actions"] = dict(summary["actions"])

    plan = {
        "schema_version": DREAM_SCHEMA_VERSION,
        "run_id": run,
        "created_at_ms": now_ms(),
        "project_slug": project_slug,
        "project_path": str(project_path) if project_path else "",
        "aliases": aliases,
        "backend": backend,
        "ledger": str(ledger),
        "index": str(index),
        "run_dir": str(run_dir),
        "review_required": True,
        "summary": summary,
        "actions": actions,
    }
    write_jsonl(run_dir / "snapshot.jsonl", target_episodes)
    write_json(run_dir / "plan.json", plan)
    return plan


def plan_command(args: argparse.Namespace) -> int:
    plan = build_plan(args)
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def load_plan(path: Path) -> dict[str, object]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan.get("schema_version") != DREAM_SCHEMA_VERSION:
        raise AgentMemoryDreamError(f"unsupported plan schema: {plan.get('schema_version')}")
    return plan


def apply_plan_payload(args: argparse.Namespace) -> dict[str, object]:
    if not args.reviewed:
        raise AgentMemoryDreamError("apply requires --reviewed")
    plan_path = Path(args.plan).expanduser().resolve()
    plan = load_plan(plan_path)
    actions = plan.get("actions", [])
    if not isinstance(actions, list):
        raise AgentMemoryDreamError("plan actions must be a list")

    updates: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        if action.get("kind") not in {"merge_slug", "archive_duplicate"}:
            skipped.append({"kind": action.get("kind"), "reason": "non-mutating action"})
            continue
        after = action.get("after")
        if not isinstance(after, dict):
            skipped.append({"kind": action.get("kind"), "reason": "missing after payload"})
            continue
        validate_episode_payload(after)
        updates.append(after)

    applied = 0
    if updates and args.write_postgres:
        for episode in updates:
            upsert_episode(args.dsn, episode)
            applied += 1

    output_dir = Path(str(plan.get("run_dir", plan_path.parent))).expanduser().resolve()
    write_jsonl(output_dir / "applied-updates.jsonl", updates)
    result = {
        "plan": str(plan_path),
        "reviewed": True,
        "write_postgres": args.write_postgres,
        "applied_updates": applied,
        "staged_updates": len(updates),
        "skipped_actions": skipped,
        "updates_path": str(output_dir / "applied-updates.jsonl"),
    }
    write_json(output_dir / "apply-result.json", result)
    return result


def apply_plan(args: argparse.Namespace) -> int:
    result = apply_plan_payload(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def add_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-slug")
    parser.add_argument("--project-path")
    parser.add_argument("--alias", action="append", default=[], help="Slug alias in FROM=TO form.")
    parser.add_argument("--alias-file")
    parser.add_argument("--source", choices=["auto", "postgres", "jsonl"], default="auto")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument("--dsn", default=default_dsn())
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--promote-threshold", type=int, default=3)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan and apply audited memory consolidation passes."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan")
    add_plan_args(plan_parser)
    plan_parser.set_defaults(func=plan_command)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--plan", required=True)
    apply_parser.add_argument("--reviewed", action="store_true")
    apply_parser.add_argument("--dsn", default=default_dsn())
    apply_parser.add_argument(
        "--no-postgres",
        action="store_true",
        help="Only write applied-updates.jsonl; do not mutate Postgres.",
    )
    apply_parser.set_defaults(func=lambda args: apply_plan(set_apply_defaults(args)))
    return parser


def set_apply_defaults(args: argparse.Namespace) -> argparse.Namespace:
    args.write_postgres = not args.no_postgres
    return args


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (
        AgentMemoryDreamError,
        AgentMemoryPostgresError,
        sqlite3.Error,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
