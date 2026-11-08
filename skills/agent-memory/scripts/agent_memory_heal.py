#!/usr/bin/env python3
# Public edition adapted from the owner's Agent Memory server; see NOTICE.
from __future__ import annotations
from memory_paths import memory_path

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from agent_memory_episode import validate_episode_payload
from agent_memory_postgres import fetch_episode_by_id, upsert_episode
from agent_memory_resource_guard import assert_guard_inactive


HEAL_DETECTOR_VERSION = "heal-v1"
HEAL_SCHEMA_VERSION = "agent-memory-heal-plan.v1"

SUPERSESSION_REASONS = (
    "policy_changed",
    "fact_corrected",
    "implementation_replaced",
    "preference_updated",
)

_UNIT = "\x1f"  # ASCII unit separator — not valid in shas/paths/ids

_POSITIVE_POLARITY = ("must ", "always ")
_NEGATIVE_POLARITY = ("never ", "avoid ", "do not ", "don't ", "deprecated ")

_META_TAG_PREFIXES = ("session:", "path:", "previous-slug:", "duplicate-of:")
_META_TAG_EXACT = {"dream-consolidated", "dream-slug-merged", "dream-archived"}
_GENERIC_TOKENS = frozenset(
    {
        "error", "false", "value", "files", "using", "system", "agent",
        "should", "could", "would", "after", "before", "where", "which",
        "their", "there", "these", "those", "while", "about", "again",
    }
)
_MIN_SHARED_TAGS = 3
_MIN_SHARED_TOKENS = 3
_MIN_TOKEN_LEN = 5


class AgentMemoryHealError(Exception):
    """Raised when a HEAL plan or apply cannot complete."""


# Helpers below (_normalize_summary, _meaningful_tags, _topical_tokens) and the
# polarity/generic-token constants are duplicated from agent_memory_dream.py.
# Contradiction detection is delegated here from the dream pass (plan_contradictions is a
# thin shim). Dream keeps its own copies of normalize_summary / meaningful_tags because
# plan_exact_duplicates and plan_pattern_promotions still use them, so the helper
# duplication is intentional on both sides.


def _normalize_summary(summary: object) -> str:
    text = str(summary).lower()
    text = re.sub(r"session[:\s]+[a-f0-9-]{8,}", "session:<id>", text)
    text = re.sub(r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b", "<date>", text)
    text = re.sub(r"\b[0-9a-f]{7,40}\b", "<hash>", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _meaningful_tags(episode: dict[str, object]) -> set[str]:
    raw = episode.get("tags", [])
    if not isinstance(raw, list):
        return set()
    return {
        str(tag)
        for tag in raw
        if str(tag) not in _META_TAG_EXACT
        and not any(str(tag).startswith(p) for p in _META_TAG_PREFIXES)
    }


def _topical_tokens(summary: str) -> set[str]:
    return {
        tok for tok in summary.split() if len(tok) >= _MIN_TOKEN_LEN
    } - _GENERIC_TOKENS


def finding_id(
    *,
    scope: str,
    newer_id: str,
    older_id: str,
    kind: str,
    detector_version: str,
) -> str:
    """Deterministic, scope+version-namespaced finding id.

    Same (scope, newer_id, older_id, kind, detector_version) → same id.
    Changing scope OR detector_version invalidates prior ids — this is intentional:
    re-runs after a detector upgrade produce fresh proposals rather than colliding
    with the previous detector's findings.
    """
    raw = _UNIT.join((scope, newer_id, older_id, kind, detector_version))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]
    return f"heal-{digest}"


def detect_lexical_contradictions(
    episodes: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Lexical contradiction detector (the first of two detectors).

    Emits flag_contradiction actions tagged detector='lexical'. Topical overlap
    gate (>=3 shared meaningful tags AND >=3 shared content tokens) prevents a
    flood of false positives from pairs that merely share a topic.
    """
    actions: list[dict[str, object]] = []
    for left_index, left in enumerate(episodes):
        left_text = _normalize_summary(left.get("summary", ""))
        left_tags = _meaningful_tags(left)
        if not left_tags:
            continue
        left_tokens = _topical_tokens(left_text)
        for right in episodes[left_index + 1:]:
            right_tags = _meaningful_tags(right)
            shared_tags = left_tags & right_tags
            if len(shared_tags) < _MIN_SHARED_TAGS:
                continue
            right_text = _normalize_summary(right.get("summary", ""))
            right_tokens = _topical_tokens(right_text)
            if len(left_tokens & right_tokens) < _MIN_SHARED_TOKENS:
                continue
            left_pos = any(term in left_text for term in _POSITIVE_POLARITY)
            left_neg = any(term in left_text for term in _NEGATIVE_POLARITY)
            right_pos = any(term in right_text for term in _POSITIVE_POLARITY)
            right_neg = any(term in right_text for term in _NEGATIVE_POLARITY)
            if not ((left_pos and right_neg) or (left_neg and right_pos)):
                continue
            scope = str(left.get("project_slug", ""))
            # flag_contradiction has no chronological semantic (both episodes are mutually
            # contradictory). Sort ids lexicographically so finding_id is stable across
            # iteration order (idempotent re-runs). The supersession
            # proposer uses chronological order instead, because it has a directional
            # semantic (newer supersedes older).
            id_left = str(left["episode_id"])
            id_right = str(right["episode_id"])
            id_a, id_b = sorted((id_left, id_right))
            summary_left = str(left.get("summary", ""))[:240]
            summary_right = str(right.get("summary", ""))[:240]
            summary_a, summary_b = (
                (summary_left, summary_right) if id_a == id_left else (summary_right, summary_left)
            )
            actions.append(
                {
                    "kind": "flag_contradiction",
                    "finding_id": finding_id(
                        scope=scope,
                        newer_id=id_a,
                        older_id=id_b,
                        kind="flag_contradiction",
                        detector_version=HEAL_DETECTOR_VERSION,
                    ),
                    "detector": "lexical",
                    "detector_version": HEAL_DETECTOR_VERSION,
                    "episode_ids": [id_a, id_b],
                    "shared_tags": sorted(shared_tags),
                    "summaries": [summary_a, summary_b],
                }
            )
    return actions


def evaluate_detector(
    *,
    detector: Callable[[list[dict[str, object]]], list[dict[str, object]]],
    fixture_path: Path,
    output_path: Path | None = None,
) -> dict[str, object]:
    """Run `detector` over a labeled pair fixture and report precision/recall.

    Fixture format: one JSON object per line with keys:
      - "label": "positive" or "negative"
      - "a": episode dict (validated)
      - "b": episode dict (validated)

    A pair counts as a true positive (TP) if `detector([a, b])` emits ≥1 action
    that names both episode_ids; false negative if the pair is positive-labeled
    and the detector emits none; false positive if a negative-labeled pair
    triggers any action; true negative if neither.
    """
    tp = fp = fn = tn = 0
    fixture = Path(fixture_path)
    if not fixture.exists():
        raise AgentMemoryHealError(f"fixture missing: {fixture}")
    with fixture.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise AgentMemoryHealError(
                    f"{fixture}:{line_number}: invalid JSON: {exc}"
                ) from exc
            label = row.get("label")
            a = row.get("a")
            b = row.get("b")
            if not isinstance(a, dict) or not isinstance(b, dict):
                raise AgentMemoryHealError(f"{fixture}:{line_number}: missing a/b")
            actions = detector([a, b])
            ids = {str(a["episode_id"]), str(b["episode_id"])}
            flagged = any(
                set(map(str, action.get("episode_ids", []))) == ids
                for action in actions
            )
            if label == "positive" and flagged:
                tp += 1
            elif label == "positive" and not flagged:
                fn += 1
            elif label == "negative" and flagged:
                fp += 1
            elif label == "negative" and not flagged:
                tn += 1
            else:
                raise AgentMemoryHealError(
                    f"{fixture}:{line_number}: unknown label {label!r}"
                )
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    report: dict[str, object] = {
        "detector_version": HEAL_DETECTOR_VERSION,
        "fixture": str(fixture),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": float(precision),
        "recall": float(recall),
    }
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def is_index_fresh_for(
    *,
    episode_id: str,
    current_payload: dict[str, object],
    embedding_index_path: Path,
    model: str,
) -> bool:
    """Check that the embed-index `text` column matches the current `episode_text`.

    Returns False if the episode is absent from the index, was indexed under a
    DIFFERENT embedding model, or its stored text diverges from what would be
    computed from current_payload. The semantic pass refuses unless ALL candidate
    episodes pass this gate.

    The ``model`` filter is required: after a re-embed with a different model the
    old row still has matching text, so ``_read_index_vector`` (which does filter
    by model) would silently return None and skip those episodes rather than
    emitting ``reindex_required``.
    """
    from agent_memory_embed_index import episode_text  # local import to avoid cycle

    path = Path(embedding_index_path)
    if not path.exists():
        return False
    uri = f"file:{path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, timeout=10.0, uri=True)
    try:
        row = conn.execute(
            "SELECT text FROM episode_embeddings "
            "WHERE episode_id = ? AND embedding_model = ?",
            (episode_id, model),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return False
    return str(row[0]) == episode_text(current_payload)


def freshness_gate(
    episodes: list[dict[str, object]],
    *,
    embedding_index_path: Path,
    model: str,
) -> tuple[list[str], list[str]]:
    """Partition the candidate corpus into fresh_ids vs stale_ids.

    A semantic detector MUST refuse if stale_ids is non-empty for its corpus.
    Callers emit a `reindex_required` action for the stale set rather than
    embedding stale text and confidently proposing wrong edges.

    ``model`` is forwarded to ``is_index_fresh_for`` so that episodes indexed
    under a different model are correctly classified as stale.
    """
    fresh: list[str] = []
    stale: list[str] = []
    for ep in episodes:
        ep_id = str(ep["episode_id"])
        if is_index_fresh_for(
            episode_id=ep_id,
            current_payload=ep,
            embedding_index_path=embedding_index_path,
            model=model,
        ):
            fresh.append(ep_id)
        else:
            stale.append(ep_id)
    return fresh, stale


_SEMANTIC_COSINE_THRESHOLD = 0.90  # Minimum similarity before topical and polarity checks.


def _read_index_vector(
    index_path: Path,
    episode_id: str,
    model: str,
) -> list[float] | None:
    if not index_path.exists():
        return None
    uri = f"file:{index_path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, timeout=10.0, uri=True)
    try:
        row = conn.execute(
            "SELECT embedding_json FROM episode_embeddings "
            "WHERE episode_id = ? AND embedding_model = ?",
            (episode_id, model),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return [float(v) for v in json.loads(row[0])]


def detect_semantic_contradictions(
    episodes: list[dict[str, object]],
    *,
    embedding_index_path: Path,
    model: str,
    ollama_url: str,
    guard_pid_file: Path,
    force: bool,
) -> list[dict[str, object]]:
    """Semantic contradiction detector.

    Gates before doing any cosine work:
      1. Resource guard — refuses if the guarded resource process looks active
         (raises ResourceGuardError; the orchestrator catches that and surfaces
         it as semantic_unavailable_reason).
      2. Freshness gate — emits reindex_required and refuses semantic findings
         for any episode whose embed-index text drifted from current payload.
    """
    assert_guard_inactive(Path(guard_pid_file), force)  # raises ResourceGuardError on hit
    from agent_memory_embed_index import cosine  # avoid module-load cost in lexical-only path

    fresh_ids, stale_ids = freshness_gate(
        episodes,
        embedding_index_path=embedding_index_path,
        model=model,
    )
    actions: list[dict[str, object]] = []
    if stale_ids:
        actions.append(
            {
                "kind": "reindex_required",
                "reason": "freshness_gate_failed",
                "stale_episode_ids": stale_ids[:50],
                "stale_count": len(stale_ids),
                "command": (
                    f"python3 scripts/agent_memory_embed_index.py index "
                    f"--source postgres --reindex --index {embedding_index_path}"
                ),
            }
        )
        return actions  # refuse semantic pass when ANY candidate is stale

    fresh_set = set(fresh_ids)
    fresh_episodes = [ep for ep in episodes if str(ep["episode_id"]) in fresh_set]
    vectors: dict[str, list[float]] = {}
    for ep in fresh_episodes:
        vec = _read_index_vector(embedding_index_path, str(ep["episode_id"]), model)
        if vec is None:
            continue
        vectors[str(ep["episode_id"])] = vec

    for left_index, left in enumerate(fresh_episodes):
        left_text = _normalize_summary(left.get("summary", ""))
        left_tags = _meaningful_tags(left)
        if not left_tags:
            continue
        left_pos = any(t in left_text for t in _POSITIVE_POLARITY)
        left_neg = any(t in left_text for t in _NEGATIVE_POLARITY)
        if not (left_pos or left_neg):
            continue
        left_vec = vectors.get(str(left["episode_id"]))
        if left_vec is None:
            continue
        for right in fresh_episodes[left_index + 1:]:
            # Require shared meaningful tags as well as vector similarity;
            # similarity alone does not establish a contradiction.
            if len(left_tags & _meaningful_tags(right)) < _MIN_SHARED_TAGS:
                continue
            right_text = _normalize_summary(right.get("summary", ""))
            right_pos = any(t in right_text for t in _POSITIVE_POLARITY)
            right_neg = any(t in right_text for t in _NEGATIVE_POLARITY)
            if not ((left_pos and right_neg) or (left_neg and right_pos)):
                continue
            right_vec = vectors.get(str(right["episode_id"]))
            if right_vec is None or len(right_vec) != len(left_vec):
                continue
            score = cosine(left_vec, right_vec)
            if score < _SEMANTIC_COSINE_THRESHOLD:
                continue
            scope = str(left.get("project_slug", ""))
            # Same iteration-order-stability discipline as the lexical detector
            # sort the two ids lexicographically before computing
            # finding_id so re-runs over the same corpus produce the same id.
            id_left = str(left["episode_id"])
            id_right = str(right["episode_id"])
            id_a, id_b = sorted((id_left, id_right))
            summary_left = str(left.get("summary", ""))[:240]
            summary_right = str(right.get("summary", ""))[:240]
            summary_a, summary_b = (
                (summary_left, summary_right) if id_a == id_left else (summary_right, summary_left)
            )
            actions.append(
                {
                    "kind": "flag_contradiction",
                    "finding_id": finding_id(
                        scope=scope,
                        newer_id=id_a,
                        older_id=id_b,
                        kind="flag_contradiction",
                        detector_version=HEAL_DETECTOR_VERSION,
                    ),
                    "detector": "semantic",
                    "detector_version": HEAL_DETECTOR_VERSION,
                    "episode_ids": [id_a, id_b],
                    "score": float(score),
                    "summaries": [summary_a, summary_b],
                }
            )
    return actions


def _stub_index_from_fixture(
    *,
    fixture_path: Path,
    index_path: Path,
    model: str,
) -> None:
    """Build a synthetic embedding index that matches the contradiction fixture.

    Used by tests to exercise the semantic detector without calling Ollama.
    For each labeled pair, both episodes get identical (1.0, 0.0, 0.0) vectors
    when label=='positive' so cosine ≈ 1; uncorrelated deterministic-from-hash
    vectors otherwise. Index `text` column is populated from episode_text so
    the freshness gate passes.
    """
    from agent_memory_embed_index import connect_index, episode_text

    conn = connect_index(index_path)
    with Path(fixture_path).open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            label = row.get("label")
            for side in ("a", "b"):
                ep = row[side]
                if label == "positive":
                    vec = [1.0, 0.0, 0.0]
                else:
                    h = hashlib.sha256(
                        (str(ep["episode_id"]) + side).encode("utf-8")
                    ).digest()
                    vec = [b / 255.0 for b in h[:3]]
                conn.execute(
                    "INSERT OR REPLACE INTO episode_embeddings "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        ep["episode_id"], ep["project_slug"], ep["scope"], ep["summary"],
                        json.dumps(ep.get("tags", [])), json.dumps(ep),
                        episode_text(ep),
                        model, len(vec), json.dumps(vec), 1,
                    ),
                )
    conn.close()


def evaluate_contradiction_pipeline(
    *,
    fixture_path: Path,
    embedding_index_path: Path,
    model: str,
    ollama_url: str,
    guard_pid_file: Path,
    force: bool,
    output_path: Path | None = None,
) -> dict[str, object]:
    """Combined eval: lexical + semantic + their union, all over one fixture.

    Returns a dict with three sub-reports keyed `lexical`, `semantic`, and
    `combined`. Writes the full report to `output_path` if provided.
    """
    lexical_report = evaluate_detector(
        detector=detect_lexical_contradictions,
        fixture_path=fixture_path,
    )

    def semantic_only(pair: list[dict[str, object]]) -> list[dict[str, object]]:
        return detect_semantic_contradictions(
            pair,
            embedding_index_path=embedding_index_path,
            model=model,
            ollama_url=ollama_url,
            guard_pid_file=guard_pid_file,
            force=force,
        )

    semantic_report = evaluate_detector(
        detector=semantic_only,
        fixture_path=fixture_path,
    )

    def combined(pair: list[dict[str, object]]) -> list[dict[str, object]]:
        return detect_lexical_contradictions(pair) + semantic_only(pair)

    combined_report = evaluate_detector(
        detector=combined,
        fixture_path=fixture_path,
    )

    report: dict[str, object] = {
        "detector_version": HEAL_DETECTOR_VERSION,
        "fixture": str(fixture_path),
        "lexical": lexical_report,
        "semantic": semantic_report,
        "combined": combined_report,
    }
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


_REASON_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "policy_changed",
        ("policy changed", "policy now", "now only fires", "new policy",
         "policy updated", "policy revision"),
    ),
    (
        "fact_corrected",
        # "actually" is deliberately absent: a common discourse word, not a correction
        # signal by itself.
        ("correction:", "incorrect", "was wrong", "fact corrected",
         "corrected:", "not true"),
    ),
    (
        "implementation_replaced",
        # "supersed*" is included: an explicit "supersedes <id>"
        # reference is itself a replacement signal — without this it would be
        # dropped at the reason gate before the explicit-confidence tier runs.
        ("now uses", "replaced", "switched to", "migrated to", "instead of",
         "no longer uses", "implementation replaced", "supersedes", "superseded",
         "supersession"),
    ),
    (
        "preference_updated",
        ("prefer ", "preference now", "from now on", "going forward",
         "preference updated", "now we use"),
    ),
)


def classify_supersession_reason(
    older: dict[str, object],
    newer: dict[str, object],
) -> str | None:
    """Return a typed supersession reason or None if no pattern matches.

    Untyped pairs are rejected by `propose_supersessions`
    (no untyped edges land in the graph).
    """
    text = _normalize_summary(newer.get("summary", ""))
    for reason, phrases in _REASON_PATTERNS:
        if any(phrase in text for phrase in phrases):
            return reason
    return None


def _supersession_is_explicit(newer: dict[str, object], older_id: str) -> bool:
    """True when the newer episode's RAW summary names the older id and 'supersed*'.

    Checked on raw (un-normalized) text on purpose: _normalize_summary rewrites
    hex ids to '<hash>', which would erase the very reference we are looking for.
    This is the strongest supersession signal — the author explicitly declared it
    (for example, a summary that names the replaced episode's identifier after the word "supersedes").
    """
    text = str(newer.get("summary", "")).lower()
    return "supersed" in text and str(older_id)[:8].lower() in text


def propose_supersessions(
    episodes: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Emit confidence-tiered propose_supersession actions for typed-reason pairs.

    Candidate gate (unchanged): same-scope, >=3 shared meaningful tags, classifier
    returns a typed reason, newer = max created_at_ms. Untyped pairs are rejected.

    Confidence tiers for explicit references and inferred replacement pairs:
      - "explicit"   — newer names the older id + 'supersed*' (author-declared).
      - "low_review" — a NON-explicit newer fans out to >1 older (over-pairing
                       smell: shared vocabulary, not a real per-pair supersession).
      - "heuristic"  — a NON-explicit newer with exactly one older candidate.
    Confidence is a pure function of the candidate set, so re-runs are stable.
    """
    # Pass 1: collect candidates (deterministic iteration order).
    candidates: list[tuple[dict[str, object], dict[str, object], str, str, list[str]]] = []
    seen: set[str] = set()
    for left_index, left in enumerate(episodes):
        left_tags = _meaningful_tags(left)
        if not left_tags:
            continue
        left_scope = str(left.get("project_slug", ""))
        for right in episodes[left_index + 1:]:
            if str(right.get("project_slug", "")) != left_scope:
                continue
            shared_tags = left_tags & _meaningful_tags(right)
            if len(shared_tags) < _MIN_SHARED_TAGS:
                continue
            if int(left.get("created_at_ms", 0)) >= int(right.get("created_at_ms", 0)):
                newer, older = left, right
            else:
                newer, older = right, left
            reason = classify_supersession_reason(older, newer)
            if reason is None:
                continue
            pair_key = f"{newer['episode_id']}{_UNIT}{older['episode_id']}"
            if pair_key in seen:
                continue
            seen.add(pair_key)
            candidates.append((newer, older, reason, left_scope, sorted(shared_tags)))

    # Pass 2: count non-explicit fan-out per newer to detect many-to-one.
    nonexplicit_fanout: dict[str, int] = {}
    for newer, older, *_ in candidates:
        if not _supersession_is_explicit(newer, str(older["episode_id"])):
            nid = str(newer["episode_id"])
            nonexplicit_fanout[nid] = nonexplicit_fanout.get(nid, 0) + 1

    actions: list[dict[str, object]] = []
    for newer, older, reason, scope, shared_tags in candidates:
        newer_id = str(newer["episode_id"])
        older_id = str(older["episode_id"])
        if _supersession_is_explicit(newer, older_id):
            confidence = "explicit"
        elif nonexplicit_fanout.get(newer_id, 0) > 1:
            confidence = "low_review"
        else:
            confidence = "heuristic"
        actions.append(
            {
                "kind": "propose_supersession",
                "finding_id": finding_id(
                    scope=scope,
                    newer_id=newer_id,
                    older_id=older_id,
                    kind="propose_supersession",
                    detector_version=HEAL_DETECTOR_VERSION,
                ),
                "detector_version": HEAL_DETECTOR_VERSION,
                "newer_episode_id": newer_id,
                "older_episode_id": older_id,
                "reason": reason,
                "confidence": confidence,
                "evidence": {
                    "shared_tags": shared_tags,
                    "newer_created_at_ms": int(newer.get("created_at_ms", 0)),
                    "older_created_at_ms": int(older.get("created_at_ms", 0)),
                    "summaries": [
                        str(newer.get("summary", ""))[:240],
                        str(older.get("summary", ""))[:240],
                    ],
                },
            }
        )
    return actions


def evaluate_supersession_proposer(
    *,
    fixture_path: Path,
    output_path: Path | None = None,
) -> dict[str, object]:
    """Run the supersession proposer over the labeled fixture.

    Reports overall precision/recall PLUS per-reason breakdown so a reviewer
    can spot which reason class is weakest (and tune accordingly in a later
    session, NOT in this build).
    """
    tp = fp = fn = tn = 0
    by_reason: dict[str, dict[str, int]] = {
        r: {"tp": 0, "fp": 0, "fn": 0} for r in SUPERSESSION_REASONS
    }
    with Path(fixture_path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            label = row.get("label")
            expected_reason = row.get("expected_reason")
            newer = row["newer"]
            older = row["older"]
            actions = propose_supersessions([older, newer])
            ss = [a for a in actions if a["kind"] == "propose_supersession"]
            ids = {str(newer["episode_id"]), str(older["episode_id"])}
            flagged = any(
                {a["newer_episode_id"], a["older_episode_id"]} == ids for a in ss
            )
            if label == "positive" and flagged:
                tp += 1
                action = next(
                    a for a in ss
                    if {a["newer_episode_id"], a["older_episode_id"]} == ids
                )
                if action["reason"] == expected_reason:
                    by_reason[expected_reason]["tp"] += 1
                else:
                    # Counted as TP overall, but missed the reason class.
                    by_reason[expected_reason]["fn"] += 1
            elif label == "positive" and not flagged:
                fn += 1
                if expected_reason in by_reason:
                    by_reason[expected_reason]["fn"] += 1
            elif label == "negative" and flagged:
                fp += 1
                action = next(
                    a for a in ss
                    if {a["newer_episode_id"], a["older_episode_id"]} == ids
                )
                if action["reason"] in by_reason:
                    by_reason[action["reason"]]["fp"] += 1
            elif label == "negative" and not flagged:
                tn += 1
            else:
                raise AgentMemoryHealError(
                    f"{fixture_path}:{line_number}: unknown label {label!r}"
                )
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    report: dict[str, object] = {
        "detector_version": HEAL_DETECTOR_VERSION,
        "fixture": str(fixture_path),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": float(precision),
        "recall": float(recall),
        "by_reason": by_reason,
    }
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def evaluate_narrow_contradiction_alarm(
    *,
    fixture_paths: list[Path],
    output_path: Path | None = None,
) -> dict[str, object]:
    """Evaluate contradiction detection before and after supersession routing.

    Over one or more labeled contradiction fixtures (rows {"a","b","label"}),
    measures the lexical contradiction detector BOTH raw and AFTER
    supersession routing (a flagged pair that is also a typed supersession is a
    progression -> its flag is dropped). Reporting raw vs routed precision/recall
    makes the routing's value visible on disk, and the fixtures MUST include the
    same-thread-progression negatives to distinguish a changed decision from
    an unresolved contradiction.
    """
    raw = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    routed = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    recovered_as_supersession = 0
    pairs = 0
    for fixture_path in fixture_paths:
        with Path(fixture_path).open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                row = json.loads(stripped)
                label = row.get("label")
                a, b = row["a"], row["b"]
                pairs += 1
                raw_flag = len(detect_lexical_contradictions([a, b])) > 0
                is_super = len(propose_supersessions([a, b])) > 0
                routed_flag = raw_flag and not is_super
                if raw_flag and is_super:
                    recovered_as_supersession += 1
                if label == "positive":
                    raw["tp" if raw_flag else "fn"] += 1
                    routed["tp" if routed_flag else "fn"] += 1
                elif label == "negative":
                    raw["fp" if raw_flag else "tn"] += 1
                    routed["fp" if routed_flag else "tn"] += 1
                else:
                    raise AgentMemoryHealError(
                        f"{fixture_path}:{line_number}: unknown label {label!r}"
                    )

    def _pr(stats: dict[str, int]) -> dict[str, object]:
        tp, fp, fn = stats["tp"], stats["fp"], stats["fn"]
        return {
            **stats,
            "precision": float(tp / (tp + fp)) if (tp + fp) else 0.0,
            "recall": float(tp / (tp + fn)) if (tp + fn) else 0.0,
        }

    report: dict[str, object] = {
        "detector_version": HEAL_DETECTOR_VERSION,
        "fixtures": [str(p) for p in fixture_paths],
        "pairs": pairs,
        "raw_lexical": _pr(raw),
        "routed_alarm": _pr(routed),
        "recovered_as_supersession": recovered_as_supersession,
    }
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def plan_heal_actions(
    *,
    episodes: list[dict[str, object]],
    embedding_index_path: Path,
    model: str,
    ollama_url: str,
    run_dir: Path,
    guard_pid_file: Path,
    force: bool,
    semantic: bool = False,
) -> dict[str, object]:
    """Top-level HEAL orchestrator (supersession-primary).

    Supersession proposal is HEAL's primary product. Contradiction detection is
    demoted to a NARROW alarm: a polarity-opposite pair that is ALSO a supersession
    is a progression (newer supersedes older), not a standing contradiction, so its
    flag is dropped and the pair surfaces only as the supersession proposal. The
    semantic contradiction detector is OFF by default and requires a fresh
    index and a local embedding service — pass
    semantic=True to enable it; otherwise the default path touches no embed index.

    Returns a plan dict shaped like dream's plan dicts. Writes:
      - <run_dir>/heal-plan.json   — the plan
      - <run_dir>/snapshot.jsonl   — input episodes (provenance)

    Does NOT mutate Postgres. Unreviewed proposals stay ephemeral here
    reviewed apply happens in apply_heal_plan.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    # Primary product first, so contradiction detection can be narrowed against it.
    supersessions = propose_supersessions(episodes)
    supersession_pairs = {
        frozenset((str(a["newer_episode_id"]), str(a["older_episode_id"])))
        for a in supersessions
    }

    contradictions: list[dict[str, object]] = list(detect_lexical_contradictions(episodes))
    non_flag_actions: list[dict[str, object]] = []
    semantic_unavailable_reason: str | None = None
    if semantic:
        # Lazy import exception classes (cold path): only loaded on the semantic path.
        # ResourceGuardError (guard active), AgentMemoryEmbedError (embedding service),
        # sqlite3.Error (corrupt index), OSError (missing file).
        from agent_memory_resource_guard import ResourceGuardError
        from agent_memory_embed_index import AgentMemoryEmbedError

        try:
            semantic_actions = detect_semantic_contradictions(
                episodes,
                embedding_index_path=embedding_index_path,
                model=model,
                ollama_url=ollama_url,
                guard_pid_file=guard_pid_file,
                force=force,
            )
            # Semantic emits flag_contradiction (narrowed below) + reindex_required (kept).
            contradictions.extend(a for a in semantic_actions if a.get("kind") == "flag_contradiction")
            non_flag_actions.extend(a for a in semantic_actions if a.get("kind") != "flag_contradiction")
        except (
            AgentMemoryHealError,
            ResourceGuardError,
            AgentMemoryEmbedError,
            sqlite3.Error,
            OSError,
        ) as exc:
            semantic_unavailable_reason = f"{type(exc).__name__}:{exc}"
    else:
        semantic_unavailable_reason = "semantic_default_off (pass semantic=True / --semantic to enable)"

    # Narrow alarm: drop contradiction flags whose pair is already a supersession
    # (those are progressions, handled by the primary product).
    narrowed_contradictions = [
        c for c in contradictions
        if frozenset(str(e) for e in c.get("episode_ids", [])) not in supersession_pairs
    ]

    actions: list[dict[str, object]] = supersessions + narrowed_contradictions + non_flag_actions

    snapshot_path = run_dir / "snapshot.jsonl"
    with snapshot_path.open("w", encoding="utf-8") as handle:
        for ep in episodes:
            handle.write(json.dumps(ep, sort_keys=True) + "\n")

    plan: dict[str, object] = {
        "schema_version": HEAL_SCHEMA_VERSION,
        "created_at_ms": int(time.time_ns() // 1_000_000),
        "detector_version": HEAL_DETECTOR_VERSION,
        "run_dir": str(run_dir),
        "review_required": True,
        "embedding_index_path": str(embedding_index_path),
        "model": model,
        "semantic_unavailable_reason": semantic_unavailable_reason,
        "actions": actions,
        "summary": {
            "input_episode_count": len(episodes),
            "action_counts": _count_kinds(actions),
            "supersession_confidence": _count_supersession_confidence(actions),
        },
    }
    (run_dir / "heal-plan.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return plan


def _count_kinds(actions: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in actions:
        kind = str(action.get("kind", "unknown"))
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _count_supersession_confidence(actions: list[dict[str, object]]) -> dict[str, int]:
    """Confidence breakdown of supersession proposals — lets triage separate the
    trustworthy tier (explicit + heuristic) from the quarantined many-to-one tier
    (low_review) at a glance, instead of eyeballing every proposal."""
    counts: dict[str, int] = {}
    for action in actions:
        if action.get("kind") != "propose_supersession":
            continue
        conf = str(action.get("confidence", "unknown"))
        counts[conf] = counts.get(conf, 0) + 1
    return counts


def apply_heal_plan(
    *,
    plan_path: Path,
    reviewed: bool,
    proposal_ids: list[str],
    dsn: str,
    write_postgres: bool,
) -> dict[str, object]:
    """Apply a reviewed HEAL plan, writing supersedes[] edges to canonical episodes.

    Stricter than dream's merge/archive apply:
      - reviewed must be True
      - proposal_ids MUST be non-empty (refuses "apply all")
      - only finding_ids present in proposal_ids are applied; the rest skipped
    """
    if not reviewed:
        raise AgentMemoryHealError("apply requires reviewed=True")
    if not proposal_ids:
        raise AgentMemoryHealError(
            "apply requires non-empty proposal_ids — 'apply all' is intentionally refused"
        )

    plan_path = Path(plan_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("schema_version") != HEAL_SCHEMA_VERSION:
        raise AgentMemoryHealError(
            f"unsupported plan schema_version: {plan.get('schema_version')!r}"
        )

    run_dir = Path(str(plan.get("run_dir", plan_path.parent)))
    snapshot_path = run_dir / "snapshot.jsonl"
    if not snapshot_path.exists():
        raise AgentMemoryHealError(f"snapshot.jsonl missing in run_dir: {run_dir}")

    by_id: dict[str, dict[str, object]] = {}
    with snapshot_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            by_id[str(payload["episode_id"])] = payload

    requested = set(proposal_ids)
    proposals = [
        a for a in plan.get("actions", [])
        if isinstance(a, dict)
        and a.get("kind") == "propose_supersession"
        and a.get("finding_id") in requested
    ]

    applied = 0
    skipped: list[dict[str, object]] = []
    provenance_rows: list[dict[str, object]] = []
    now_ms_value = int(time.time_ns() // 1_000_000)

    for action in proposals:
        newer_id = str(action["newer_episode_id"])
        older_id = str(action["older_episode_id"])
        reason = str(action["reason"])
        finding = str(action["finding_id"])
        if reason not in SUPERSESSION_REASONS:
            skipped.append({"finding_id": finding, "reason": f"untyped_reason:{reason}"})
            continue
        newer = by_id.get(newer_id)
        if newer is None:
            skipped.append({"finding_id": finding, "reason": "newer_not_in_snapshot"})
            continue
        # Drift guard: fetch the current canonical episode from Postgres and
        # compare it to what was captured in snapshot.jsonl at plan time.
        # If they differ, another system mutated the episode after the snapshot
        # was taken — refuse to write a supersession edge that would silently
        # overwrite those changes (refuse-on-drift, not refetch-and-merge).
        # In dry-run mode we still attempt the fetch; if Postgres is unreachable
        # we warn and proceed so the user can see what *would* have been applied.
        current_canonical = fetch_episode_by_id(dsn, newer_id)
        if current_canonical is not None:
            if json.dumps(current_canonical, sort_keys=True) != json.dumps(newer, sort_keys=True):
                skipped.append({"finding_id": finding, "reason": "snapshot_drift"})
                continue
        elif write_postgres:
            # Postgres reachable but episode missing — treat as drift (plan is stale).
            skipped.append({"finding_id": finding, "reason": "snapshot_drift"})
            continue
        # current_canonical is None + write_postgres=False → dry-run, DB unreachable;
        # proceed with a warning recorded in skipped so the caller can see it.
        elif not write_postgres:
            skipped.append({
                "finding_id": finding,
                "reason": "drift_check_inconclusive",
                "note": "postgres unreachable in dry-run; proceeding without drift verification",
            })
        updated = dict(newer)
        supersedes_list = list(updated.get("supersedes", []))
        if older_id not in supersedes_list:
            supersedes_list.append(older_id)
        updated["supersedes"] = supersedes_list
        tags = list(updated.get("tags", []))
        for tag in (
            f"supersedes-reason:{reason}",
            f"superseded-by-finding:{finding}",
        ):
            if tag not in tags:
                tags.append(tag)
        updated["tags"] = tags
        validate_episode_payload(updated)
        if write_postgres:
            upsert_episode(dsn, updated)
        applied += 1
        provenance_rows.append(
            {
                "finding_id": finding,
                "newer_episode_id": newer_id,
                "older_episode_id": older_id,
                "reason": reason,
                "applied_at_ms": now_ms_value,
                "write_postgres": write_postgres,
            }
        )

    provenance_path = run_dir / "applied-supersessions.jsonl"
    with provenance_path.open("a", encoding="utf-8") as handle:
        for row in provenance_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    result: dict[str, object] = {
        "plan": str(plan_path),
        "reviewed": True,
        "write_postgres": write_postgres,
        "applied_updates": applied,
        "requested_proposal_ids": sorted(requested),
        "skipped_actions": skipped,
        "provenance_path": str(provenance_path),
    }
    (run_dir / "heal-apply-result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


# --------------------------------------------------------------------------- #
# Supersession adjudicator — an LLM reads each candidate and decides
# SUPERSEDES / INDEPENDENT / UNCERTAIN with a plain-English explanation, so the
# system can auto-apply the confident ones, auto-discard independents, and surface
# only genuine doubt. Writes still go ONLY through apply_heal_plan (reversible,
# provenance, drift-safe).
# --------------------------------------------------------------------------- #
ADJUDICATOR_PROMPT = """Adjudicate whether a NEWER memory episode SUPERSEDES an OLDER one, or they are INDEPENDENT facts in a durable memory system.

OLDER (earlier in time):
{older}

NEWER (later in time):
{newer}

SUPERSEDES = the newer fact corrects, replaces, kills, or invalidates the older one; a future reader should treat the older as no-longer-current.
INDEPENDENT = different things, or both still true (e.g. different projects/scope, or the newer merely builds on the older without invalidating it).
UNCERTAIN = genuinely ambiguous from the text.

Respond with ONLY a JSON object, no prose:
{{"verdict":"SUPERSEDES|INDEPENDENT|UNCERTAIN","confidence":"high|medium|low","explanation":"one plain-English sentence a non-engineer understands: what the older claimed and what the newer changes, OR why they are independent"}}"""

_ADJ_VERDICTS = ("SUPERSEDES", "INDEPENDENT", "UNCERTAIN")
_ADJ_CONFIDENCE = ("high", "medium", "low")


def _claude_runner(prompt: str, model: str = "", timeout: int = 120) -> str:
    """Default adjudicator LLM call: `claude -p` (host-configured model), JSON out. Strips CLAUDECODE
    so the nested session is allowed. Injectable
    via the `runner` arg everywhere so tests never spawn a subprocess."""
    if os.environ.get("FORGE_MEMORY_ALLOW_MODEL_CALLS") != "1":
        raise AgentMemoryHealError("Model adjudication is disabled; explicitly enable model calls first")
    excluded = {"CLAUDECODE", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_CLAUDE_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"}
    env = {k: v for k, v in os.environ.items() if k not in excluded}
    try:
        auth = subprocess.run(["claude", "auth", "status", "--json"], capture_output=True, text=True, timeout=15, env=env, check=False)
        status = json.loads(auth.stdout)
        if auth.returncode or status.get("authMethod") != "claude.ai" or not status.get("loggedIn"):
            raise AgentMemoryHealError("Sign in to Claude CLI with your subscription; API fallback is disabled")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise AgentMemoryHealError("Cannot verify subscription CLI authentication") from exc
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, *(["--model", model] if model else []), "--output-format", "json", "--max-turns", "1", "--tools", "", "--setting-sources", "project", "--strict-mcp-config", "--mcp-config", "{\"mcpServers\":{}}", "--no-session-persistence"],
            capture_output=True, text=True, timeout=timeout, env=env, cwd=tempfile.gettempdir(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AgentMemoryHealError(f"adjudicator LLM unavailable: {exc}") from exc
    if result.returncode != 0:
        raise AgentMemoryHealError(f"adjudicator LLM exited {result.returncode}: {result.stderr[:200]}")
    try:
        return json.loads(result.stdout).get("result", "") or ""
    except json.JSONDecodeError as exc:
        raise AgentMemoryHealError(f"adjudicator LLM returned non-JSON: {exc}") from exc


def _parse_adjudicator_json(raw: str) -> dict[str, object]:
    """Strictly parse the adjudicator response as a single JSON object.

    The WHOLE stripped response must be one JSON object — prose-wrapped or
    injection-style output like `OK {"verdict":"SUPERSEDES",...}` is REJECTED
    (returns {} -> UNCERTAIN -> never auto-applies). A single ```json fence is
    tolerated, but nothing else; this is an auto-write path, so it errs toward
    the human, never toward a write on ambiguous text."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text[:4].lower() == "json":
            text = text[4:].strip()
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def adjudicate_pair(
    older_summary: str,
    newer_summary: str,
    *,
    runner: Callable[[str, str], str] = _claude_runner,
    model: str = "",
) -> dict[str, str]:
    """Return {verdict, confidence, explanation} for one candidate. Any malformed
    LLM output degrades to UNCERTAIN/low (never silently to a write-eligible verdict)."""
    raw = runner(ADJUDICATOR_PROMPT.format(older=str(older_summary)[:700], newer=str(newer_summary)[:700]), model)
    obj = _parse_adjudicator_json(raw)
    verdict = str(obj.get("verdict", "UNCERTAIN")).upper()
    confidence = str(obj.get("confidence", "low")).lower()
    return {
        "verdict": verdict if verdict in _ADJ_VERDICTS else "UNCERTAIN",
        "confidence": confidence if confidence in _ADJ_CONFIDENCE else "low",
        "explanation": str(obj.get("explanation", ""))[:300] or "adjudicator output unparseable",
    }


def classify_adjudication(verdict_obj: dict[str, str]) -> str:
    """Route a verdict to auto_apply / discard / surface.

    Only HIGH-confidence verdicts leave the human's queue: SUPERSEDES+high
    auto-applies, INDEPENDENT+high auto-discards. EVERYTHING ELSE surfaces —
    including low/medium-confidence INDEPENDENT (a doubtful "these are
    independent" must not silently hide a real supersession) and any UNCERTAIN.
    This is the literal "surface only doubt" contract: doubt never auto-resolves."""
    verdict, confidence = verdict_obj.get("verdict"), verdict_obj.get("confidence")
    if verdict == "SUPERSEDES" and confidence == "high":
        return "auto_apply"
    if verdict == "INDEPENDENT" and confidence == "high":
        return "discard"
    return "surface"


def adjudicate_plan(
    plan: dict[str, object],
    episodes: list[dict[str, object]],
    *,
    runner: Callable[[str, str], str] = _claude_runner,
    model: str = "",
) -> dict[str, list[dict[str, object]]]:
    """Adjudicate every propose_supersession in a plan into auto_apply/discard/surface."""
    by_id = {str(e.get("episode_id", "")): e for e in episodes}
    buckets: dict[str, list[dict[str, object]]] = {"auto_apply": [], "discard": [], "surface": []}
    for action in plan.get("actions", []):
        if action.get("kind") != "propose_supersession":
            continue
        older = by_id.get(str(action.get("older_episode_id", "")), {})
        newer = by_id.get(str(action.get("newer_episode_id", "")), {})
        verdict = adjudicate_pair(str(older.get("summary", "")), str(newer.get("summary", "")),
                                  runner=runner, model=model)
        buckets[classify_adjudication(verdict)].append({
            "finding_id": action.get("finding_id"),
            "older_episode_id": action.get("older_episode_id"),
            "newer_episode_id": action.get("newer_episode_id"),
            "tag_confidence": action.get("confidence"),
            **verdict,
        })
    return buckets


def auto_heal(
    *,
    plan_path: Path,
    dsn: str,
    runner: Callable[[str, str], str] = _claude_runner,
    model: str = "",
    dry_run: bool = True,
    write_postgres: bool = True,
) -> dict[str, object]:
    """Adjudicate a plan, then (unless dry_run) auto-apply ONLY the SUPERSEDES+high
    bucket via the audited apply_heal_plan. Independents are discarded (no write);
    uncertain/medium are surfaced. Writes a manifest next to the plan for the gardener.
    The first run on any plan should be dry_run=True so the operator previews the split."""
    plan_path = Path(plan_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    snapshot = plan_path.parent / "snapshot.jsonl"
    episodes = [json.loads(line) for line in snapshot.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if snapshot.exists() else []
    buckets = adjudicate_plan(plan, episodes, runner=runner, model=model)
    manifest: dict[str, object] = {
        "schema_version": "agent-memory-adjudication.v1",
        "dry_run": dry_run,
        "model": model,
        "counts": {k: len(v) for k, v in buckets.items()},
        **buckets,
    }
    if not dry_run and buckets["auto_apply"]:
        manifest["apply_result"] = apply_heal_plan(
            plan_path=plan_path,
            reviewed=True,
            proposal_ids=[str(b["finding_id"]) for b in buckets["auto_apply"]],
            dsn=dsn,
            write_postgres=write_postgres,
        )
    (plan_path.parent / "adjudication-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _cli_plan(args: argparse.Namespace) -> int:
    from agent_memory_postgres import fetch_episodes

    episodes = fetch_episodes(args.dsn, [args.project_slug] if args.project_slug else None, args.limit)
    plan = plan_heal_actions(
        episodes=episodes,
        embedding_index_path=Path(args.index).expanduser().resolve(),
        model=args.model,
        ollama_url=args.ollama_url,
        run_dir=Path(args.run_dir).expanduser().resolve(),
        guard_pid_file=Path(args.guard_pid_file),
        force=args.force,
        semantic=args.semantic,
    )
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def _cli_apply(args: argparse.Namespace) -> int:
    result = apply_heal_plan(
        plan_path=Path(args.plan).expanduser().resolve(),
        reviewed=args.reviewed,
        proposal_ids=list(args.proposal_id or []),
        dsn=args.dsn,
        write_postgres=not args.no_postgres,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cli_adjudicate(args: argparse.Namespace) -> int:
    manifest = auto_heal(
        plan_path=Path(args.plan).expanduser().resolve(),
        dsn=args.dsn,
        model=args.model,
        dry_run=not args.apply,
        write_postgres=not args.no_postgres,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Memory drift detector and reviewed supersession writer.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_p = subparsers.add_parser("plan", help="Build a heal plan in run_dir.")
    plan_p.add_argument("--project-slug")
    plan_p.add_argument("--dsn", default="postgresql:///forge_agent_memory")
    plan_p.add_argument("--index", default=memory_path('episode_embeddings.sqlite'))
    plan_p.add_argument("--model", default="nomic-embed-text")
    plan_p.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    plan_p.add_argument("--run-dir", required=True)
    plan_p.add_argument("--guard-pid-file", default=memory_path('resource.pid'))
    plan_p.add_argument("--force", action="store_true")
    plan_p.add_argument(
        "--semantic",
        action="store_true",
        help="Enable semantic contradiction detection (off by default; requires a fresh "
             "embedding index and a local embedding service).",
    )
    plan_p.add_argument("--limit", type=int, default=None)
    plan_p.set_defaults(func=_cli_plan)

    apply_p = subparsers.add_parser("apply", help="Apply a reviewed heal plan.")
    apply_p.add_argument("--plan", required=True)
    apply_p.add_argument("--reviewed", action="store_true")
    apply_p.add_argument(
        "--proposal-id",
        action="append",
        default=[],
        help="Explicit finding_id to apply. Required (repeat). 'Apply all' is refused.",
    )
    apply_p.add_argument("--dsn", default="postgresql:///forge_agent_memory")
    apply_p.add_argument("--no-postgres", action="store_true")
    apply_p.set_defaults(func=_cli_apply)

    adj_p = subparsers.add_parser(
        "adjudicate",
        help="LLM-adjudicate a plan's supersessions: auto-apply confident, discard "
             "independents, surface only doubt. Dry-run by default; pass --apply to write.",
    )
    adj_p.add_argument("--plan", required=True)
    adj_p.add_argument("--dsn", default="postgresql:///forge_agent_memory")
    adj_p.add_argument("--model", default="")
    adj_p.add_argument("--apply", action="store_true",
                       help="Actually auto-apply the SUPERSEDES+high bucket (default is dry-run preview).")
    adj_p.add_argument("--no-postgres", action="store_true")
    adj_p.set_defaults(func=_cli_adjudicate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AgentMemoryHealError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
