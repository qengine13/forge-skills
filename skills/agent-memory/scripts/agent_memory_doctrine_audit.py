#!/usr/bin/env python3
# Public edition adapted from the owner's Agent Memory server; see NOTICE.
from __future__ import annotations
from memory_paths import memory_path

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from agent_memory_dream import write_json
from agent_memory_episode import SCHEMA_VERSION, validate_episode_payload
from agent_memory_history_scan import claude_message_text, parse_jsonl
from agent_memory_postgres import (
    AgentMemoryPostgresError,
    default_dsn,
    fetch_episodes,
    upsert_episode,
)
from agent_memory_retro_import import redact


AUDIT_SCHEMA_VERSION = "agent-memory-doctrine-audit.v1"
AUDIT_PROJECT_SLUG = "_audit-doctrine"
AUDIT_TAG = "doctrine-audit"
AUDIT_RUN_TAG = "doctrine-audit-run"
AUDIT_SCOPE = "global"
AUDIT_SOURCE = "doctrine-audit"
DEFAULT_RUN_ROOT = Path(memory_path('doctrine-audit-runs'))
DEFAULT_AUDIT_INDEX = Path(memory_path('doctrine-audit-episode_embeddings.sqlite'))
DEFAULT_BASELINE_FILE = Path("docs/doctrine-audit-baseline.json")
DEFAULT_AUTO_SESSION_LIMIT = 20

CONF_VIOLATION = "violation"
CONF_ADVISORY = "advisory"
STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"
STATUS_WAIVED = "waived"

RISK_REVIEW = "risk_review"
PLAN_REVIEW = "plan_review"
COMPLETION_REVIEW = "completion_review"
DECISION_CAPTURE = "decision_capture"

LOW_SIGNAL_COMMIT_TYPES = {"chore", "style", "refactor", "docs", "test", "build", "ci"}
DECISION_CAPTURE_LINE_THRESHOLD = 50
UNCOMMITTED_REVIEW_CREDIT_MS = 2 * 60 * 60 * 1000

COMPLETION_TERMS = (
    "complete",
    "completed",
    "done",
    "finish",
    "finished",
    "implement",
    "implemented",
    "ship",
    "shipped",
)
COMPLETION_TERM_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(term) for term in COMPLETION_TERMS) + r")\b"
)
COMPLETION_COMMIT_TYPES = {"feat", "fix"}
COMPLETION_PREFIXES = ("feat:", "fix:")
COMPLETION_ARTIFACT_PREFIXES = (
    "docs/completion-audit/",
    "docs/completion-hygiene/",
)
PLAN_HYGIENE_ARTIFACT_PREFIXES = (
    "docs/pre-flight/",
    "docs/plan-hygiene/",
)
AUDIT_SELF_LOOP_PATHS = {"skills/agent-memory/scripts/agent_memory_doctrine_audit.py"}
AUDIT_SELF_LOOP_PREFIXES = (
    "docs/doctrine-audit/",
    "docs/completion-audit/",
    "docs/completion-hygiene/",
)
AUDIT_SELF_LOOP_TEST_PATHS = {"tests/test_memory_audit.py"}
RISK_PATH_COMPONENTS = {
    "auth",
    "billing",
    "database",
    "live",
    "memory",
    "migration",
    "migrations",
    "prod",
    "production",
    "secret",
    "secrets",
}
# Project-specific risk paths (exact file names and path prefixes) belong in each project's own
# configuration. The public edition ships none, so only the component rules below apply.
RISK_PATH_EXACT_NAMES: frozenset[str] = frozenset()
RISK_PATH_PREFIXES: tuple[str, ...] = ()
SESSION_PLAN_TERMS = (
    "exit plan mode",
    "exitplanmode",
    "architecture-bearing",
    "implementation plan",
    "plan mode",
)
PLAN_HYGIENE_TERMS = ("plan-hygiene", "pre-flight verdict", "preflight verdict")
CODEX_REVIEW_TERMS = ("codex review",)
REVIEW_REF_CONTEXT_LINES = 3
HEX_RE = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)
SESSION_REF_RE = re.compile(r"\bsession(?:_id)?\s*[:=]\s*['\"]?([A-Za-z0-9][A-Za-z0-9_.:-]*)")
CODEX_ROLLOUT_TEXT_KEYS = {
    "arguments",
    "body",
    "content",
    "input_text",
    "message",
    "output",
    "output_text",
    "overall_explanation",
    "result",
    "summary",
    "text",
    "title",
    "user_facing_hint",
}
CODEX_ROLLOUT_SKIP_KEYS = {
    "base_instructions",
    "instructions",
    "tools",
}
MAX_CODEX_ROLLOUT_TEXT_VALUE = 4000
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[^'\"\s]+"
)
SECRET_LITERAL_RE = re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b")


class AgentMemoryDoctrineAuditError(Exception):
    """Raised when the doctrine audit cannot be planned or applied safely."""


@dataclass(frozen=True, slots=True)
class GitCommit:
    sha: str
    subject: str
    changed_files: list[str]
    total_lines: int
    committed_at_ms: int = 0


@dataclass(frozen=True, slots=True)
class SessionSignals:
    session_id: str
    observed_at_ms: int
    has_codex_review: bool
    has_uncommitted_review: bool
    uncommitted_reviewed_at_ms: tuple[int, ...]
    has_plan_hygiene: bool
    has_plan_mode: bool
    review_refs: set[str]
    text: str


@dataclass(frozen=True, slots=True)
class Finding:
    gate_id: str
    repo_id: str
    repo_root: str
    offending_ref: str
    confidence: str
    summary: str
    evidence: list[str]
    files_touched: list[str]


@dataclass(frozen=True, slots=True)
class ClaudeToolResult:
    tool_id: str
    text: str
    has_explicit_id: bool


FetchEpisodesFn = Callable[[str, list[str] | None, int | None], list[dict[str, object]]]
UpsertFindingsFn = Callable[[str, Sequence[dict[str, object]]], int]


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def run_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}"


def compact_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def audit_redact(text: object) -> str:
    value = str(text)
    value = SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        value,
    )
    value = SECRET_LITERAL_RE.sub("[REDACTED_SECRET]", value)
    return redact(value)


def _repo_identity_for(repo_root: str) -> str:
    resolved = str(Path(repo_root).expanduser().resolve())
    try:
        root_commits = [
            line.strip()
            for line in _run_git(resolved, ["rev-list", "--max-parents=0", "HEAD"]).splitlines()
            if line.strip()
        ]
    except (AgentMemoryDoctrineAuditError, OSError):
        root_commits = []
    if root_commits:
        return "git-root:" + ",".join(sorted(root_commits))

    try:
        remote_url = _run_git(resolved, ["config", "--get", "remote.origin.url"]).strip()
    except (AgentMemoryDoctrineAuditError, OSError):
        remote_url = ""
    if remote_url:
        return "git-origin:" + remote_url.lower().removesuffix(".git")

    return f"path:{resolved}"


def repo_id_for(repo_root: str) -> str:
    return hashlib.sha256(_repo_identity_for(repo_root).encode("utf-8")).hexdigest()[:12]


def finding_id(repo_id: str, gate_id: str, offending_ref: str) -> str:
    key = f"{repo_id}:{gate_id}:{offending_ref}"
    return "da-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]


def _run_git(repo_root: str, args: list[str]) -> str:
    cwd = Path(repo_root).expanduser().resolve()
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise AgentMemoryDoctrineAuditError(detail) from exc
    return completed.stdout


def git_toplevel(repo_root: str) -> str:
    return _run_git(repo_root, ["rev-parse", "--show-toplevel"]).strip()


def unique_git_toplevels(repo_roots: Sequence[str]) -> list[str]:
    tops: list[str] = []
    seen: set[str] = set()
    for repo in repo_roots:
        top = git_toplevel(repo)
        if top in seen:
            continue
        seen.add(top)
        tops.append(top)
    return tops


def read_git_window(repo_root: str, since_days: int) -> list[GitCommit]:
    top = git_toplevel(repo_root)
    output = _run_git(
        top,
        [
            "log",
            f"--since={max(1, since_days)} days ago",
            "--numstat",
            "--format=%x1e%H%x1f%ct%x1f%s",
        ],
    )
    commits: list[GitCommit] = []
    # str.splitlines() treats ASCII record separators as line breaks. Split on
    # the git record marker first, then parse normal lines inside each record.
    for record in output.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        lines = record.splitlines()
        if not lines:
            continue
        header = lines[0]
        header_parts = header.split("\x1f", 2)
        sha = header_parts[0]
        committed_at_ms = 0
        if len(header_parts) >= 3 and header_parts[1].isdigit():
            committed_at_ms = int(header_parts[1]) * 1000
            subject = header_parts[2]
        else:
            subject = header_parts[1] if len(header_parts) > 1 else ""
        changed_files: list[str] = []
        total_lines = 0
        for line in lines[1:]:
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            added, deleted, path = parts[0], parts[1], parts[2]
            changed_files.append(path)
            for value in (added, deleted):
                if value.isdigit():
                    total_lines += int(value)
        commits.append(
            GitCommit(
                sha=sha,
                subject=subject,
                changed_files=changed_files,
                total_lines=total_lines,
                committed_at_ms=committed_at_ms,
            )
        )
    return commits


def _commit_type(subject: str) -> str:
    first = subject.split(":", 1)[0].strip().lower()
    return first.split("(", 1)[0].strip().rstrip("!")


def _is_completion_commit(commit: GitCommit) -> bool:
    subject = commit.subject.lower()
    return (
        _commit_type(commit.subject) in COMPLETION_COMMIT_TYPES
        or subject.startswith(COMPLETION_PREFIXES)
        or COMPLETION_TERM_RE.search(subject) is not None
    )


def _has_completion_artifact(commit: GitCommit) -> bool:
    return any(path.startswith(COMPLETION_ARTIFACT_PREFIXES) for path in commit.changed_files)


def _has_plan_hygiene_artifact(commit: GitCommit) -> bool:
    return any(path.startswith(PLAN_HYGIENE_ARTIFACT_PREFIXES) for path in commit.changed_files)


def _touches_risk_path(commit: GitCommit) -> bool:
    return any(_is_risk_path(path) for path in commit.changed_files)


def _is_risk_path(path: str) -> bool:
    normalized = _normalize_path_ref(path).lstrip("/")
    if not normalized:
        return False
    if any(normalized == name or normalized.endswith(f"/{name}") for name in RISK_PATH_EXACT_NAMES):
        return True
    if any(normalized.startswith(prefix) or f"/{prefix}" in normalized for prefix in RISK_PATH_PREFIXES):
        return True
    # Match directory names and complete file stems, not arbitrary name fragments.
    components = set(normalized.split("/"))
    components.add(Path(normalized).stem)
    return bool(components & RISK_PATH_COMPONENTS)


def _is_decision_bearing(commit: GitCommit) -> bool:
    commit_type = _commit_type(commit.subject)
    return commit_type not in LOW_SIGNAL_COMMIT_TYPES and commit.total_lines > DECISION_CAPTURE_LINE_THRESHOLD


def _is_audit_self_loop_path(path: str, subject: str) -> bool:
    normalized = path.lower()
    if normalized in AUDIT_SELF_LOOP_PATHS:
        return True
    if normalized.startswith(AUDIT_SELF_LOOP_PREFIXES):
        return True
    return normalized in AUDIT_SELF_LOOP_TEST_PATHS and "doctrine-audit" in subject.lower()


def _is_audit_self_loop_commit(commit: GitCommit) -> bool:
    return bool(commit.changed_files) and all(
        _is_audit_self_loop_path(path, commit.subject)
        for path in commit.changed_files
    )


def _short_evidence(commit: GitCommit) -> list[str]:
    return [
        f"commit:{commit.sha}",
        f"subject:{commit.subject}",
        f"lines:{commit.total_lines}",
        "files:" + ",".join(commit.changed_files[:5]),
    ]


def _uncommitted_review_covered_refs(
    commits: Sequence[GitCommit],
    reviewed_at_ms_values: Sequence[int],
) -> set[str]:
    ordered_commits = sorted(
        (commit for commit in commits if commit.committed_at_ms > 0),
        key=lambda commit: (commit.committed_at_ms, commit.sha),
    )
    covered: set[str] = set()
    for reviewed_at_ms in sorted(value for value in reviewed_at_ms_values if value > 0):
        for commit in ordered_commits:
            delta_ms = commit.committed_at_ms - reviewed_at_ms
            if delta_ms < 0:
                continue
            if delta_ms > UNCOMMITTED_REVIEW_CREDIT_MS:
                break
            covered.add(commit.sha)
            break
    return covered


def detect_completion_review(
    commits: list[GitCommit],
    completion_hygiene_refs: set[str],
) -> list[Finding]:
    findings: list[Finding] = []
    for commit in commits:
        if not _is_completion_commit(commit):
            continue
        if _has_completion_artifact(commit):
            continue
        if _ref_matches(commit.sha, completion_hygiene_refs):
            continue
        findings.append(
            Finding(
                gate_id=COMPLETION_REVIEW,
                repo_id="",
                repo_root="",
                offending_ref=commit.sha,
                confidence=CONF_VIOLATION,
                summary=(
                    f"COMPLETION_REVIEW completion-hygiene gap: completion-bearing commit "
                    f"'{commit.subject}' has no completion audit artifact or capture."
                ),
                evidence=_short_evidence(commit),
                files_touched=commit.changed_files,
            )
        )
    return findings


def detect_risk_review(
    commits: list[GitCommit],
    reviewed_refs: set[str],
    force_risk_path: bool = False,
    uncommitted_reviewed_at_ms: Sequence[int] = (),
) -> list[Finding]:
    findings: list[Finding] = []
    risk_commits = [
        commit for commit in commits
        if force_risk_path or _touches_risk_path(commit)
    ]
    uncommitted_reviewed_refs = _uncommitted_review_covered_refs(
        risk_commits,
        uncommitted_reviewed_at_ms,
    )
    for commit in commits:
        if not force_risk_path and not _touches_risk_path(commit):
            continue
        if _ref_matches(commit.sha, reviewed_refs) or commit.sha in uncommitted_reviewed_refs:
            continue
        findings.append(
            Finding(
                gate_id=RISK_REVIEW,
                repo_id="",
                repo_root="",
                offending_ref=commit.sha,
                confidence=CONF_VIOLATION,
                summary=(
                    f"RISK_REVIEW risk-path review gap: risk-bearing commit "
                    f"'{commit.subject}' has no codex review signal in the scanned sessions."
                ),
                evidence=_short_evidence(commit),
                files_touched=commit.changed_files,
            )
        )
    return findings


def detect_decision_capture(
    commits: list[GitCommit],
    commits_with_capture: set[str],
    files_with_capture: set[str] | dict[str, int] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    captured_files = files_with_capture or set()
    for commit in commits:
        if _is_audit_self_loop_commit(commit):
            continue
        if not _is_decision_bearing(commit):
            continue
        if commit.sha in commits_with_capture or any(commit.sha.startswith(ref) for ref in commits_with_capture):
            continue
        if _commit_files_match_capture(commit, captured_files):
            continue
        findings.append(
            Finding(
                gate_id=DECISION_CAPTURE,
                repo_id="",
                repo_root="",
                offending_ref=commit.sha,
                confidence=CONF_ADVISORY,
                summary=(
                    f"Decision-capture memory-capture gap: decision-bearing commit "
                    f"'{commit.subject}' has no matching agent-memory capture."
                ),
                evidence=_short_evidence(commit),
                files_touched=commit.changed_files,
            )
        )
    return findings


def detect_plan_review(
    session: SessionSignals,
    repo_id: str,
    repo_root: str,
    plan_hygiene_session_refs: set[str] | None = None,
) -> list[Finding]:
    session_refs = plan_hygiene_session_refs or set()
    if (
        not session.has_plan_mode
        or session.has_plan_hygiene
        or session.session_id.lower() in session_refs
    ):
        return []
    return [
        Finding(
            gate_id=PLAN_REVIEW,
            repo_id=repo_id,
            repo_root=repo_root,
            offending_ref=f"session:{session.session_id}",
            confidence=CONF_VIOLATION,
            summary=(
                f"PLAN_REVIEW plan-hygiene gap: session {session.session_id} shows plan-mode "
                "or architecture-bearing planning with no plan-hygiene signal."
            ),
            evidence=[f"session:{session.session_id}", "signal:plan-mode", "missing:plan-hygiene"],
            files_touched=[],
        )
    ]


def _review_reference_text(text: str) -> str:
    lines = text.splitlines()
    selected: list[str] = []
    for index, line in enumerate(lines):
        if not _line_is_codex_review_evidence(line):
            continue
        selected.extend(lines[index : index + REVIEW_REF_CONTEXT_LINES + 1])
    return "\n".join(selected)


def _review_output_reference_text(output: str) -> str:
    reference_text = _review_reference_text(output)
    return audit_redact(reference_text) if reference_text.strip() else ""


def _command_from_text_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return ""
        if isinstance(payload, dict):
            return str(payload.get("cmd") or payload.get("command") or "").strip()
    return stripped


def _structured_command_from_text_line(line: str) -> str:
    stripped = line.strip()
    if not stripped.startswith("{"):
        return ""
    return _command_from_text_line(line)


def _strip_shell_assignment_prefix(command: str) -> str:
    try:
        words = shlex.split(command)
    except ValueError:
        return command.strip()
    index = 0
    while index < len(words) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[index]):
        index += 1
    return shlex.join(words[index:]) if index else command.strip()


def _command_segments(command: str) -> list[str]:
    """Split simple shell steps while preserving quoted arguments.

    This is evidence extraction, not a shell interpreter. Unbalanced quotes
    and substitutions are intentionally unsupported and receive no credit.
    """
    if "$(" in command or "`" in command or "<<" in command:
        return []
    command = command.replace("\\\n", "")
    segments: list[str] = []
    start = index = 0
    quote = ""
    escaped = False
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
        elif char in {"'", '"'}:
            quote = char
        elif char in ";\n" or command[index:index + 2] in {"&&", "||"}:
            segments.append(command[start:index])
            if char in "&|":
                index += 1
            start = index + 1
        index += 1
    if quote or escaped:
        return []
    segments.append(command[start:])
    return [_strip_shell_assignment_prefix(part).strip().lower() for part in segments if part.strip()]


def _actual_command_step(command: str, prefixes: tuple[str, ...]) -> str:
    for segment in _command_segments(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            continue
        for prefix in prefixes:
            expected = shlex.split(prefix)
            if words[:len(expected)] == expected:
                return segment
    return ""


def _command_has_actual_step(command: str, prefixes: tuple[str, ...]) -> bool:
    return bool(_actual_command_step(command, prefixes))


def _is_actual_codex_review_command(command: str) -> bool:
    return _command_has_actual_step(command, ("codex review",))


def _is_codex_review_result_line(line: str) -> bool:
    lowered = line.lower()
    stripped = lowered.strip()
    if not (
        re.match(r"codex review(?:\s|$)", stripped)
        or stripped.startswith("full review comments")
        or stripped.startswith("review comments")
    ):
        return False
    return any(
        term in lowered
        for term in (
            "completed",
            "succeeded",
            "passed",
            "no findings",
            "full review comments",
            "review comments",
            "clean review",
        )
    )


def _line_is_codex_review_evidence(line: str) -> bool:
    return _is_codex_review_result_line(line)


def _review_evidence_lines(text: str) -> list[str]:
    return [
        line for line in text.splitlines()
        if _line_is_codex_review_evidence(line)
    ]


def _is_actual_plan_hygiene_command(command: str) -> bool:
    return _command_has_actual_step(command, ("plan-hygiene", "/plan-hygiene", "$plan-hygiene"))


def _looks_like_shell_command(line: str, command: str) -> bool:
    if line.strip().startswith(("{", "[", '"', "'")):
        return True
    words = _strip_shell_assignment_prefix(command).split(maxsplit=1)
    if not words:
        return bool(command.strip())
    return words[0].lower() in {
        "cat", "grep", "head", "nl", "python", "python3", "rg", "sed", "tail",
        "echo", "printf", "git", "sh", "bash", "zsh", "env", "export", "cd",
    }


def _line_is_plan_hygiene_evidence(line: str) -> bool:
    command = _command_from_text_line(line)
    if _looks_like_shell_command(line, command):
        return False
    lowered = line.lower()
    if any(
        term in lowered
        for term in (
            "missing: pre-flight verdict",
            "missing pre-flight verdict",
            "no pre-flight verdict",
            "without pre-flight verdict",
            "skipped pre-flight verdict",
            "missing: preflight verdict",
            "missing preflight verdict",
            "no preflight verdict",
            "without preflight verdict",
            "skipped preflight verdict",
        )
    ):
        return False
    return (
        ("pre-flight verdict" in lowered or "preflight verdict" in lowered)
        and any(term in lowered for term in ("completed", "done", "pass", "produced", "written"))
    )


def _review_output_completed(output: str) -> bool:
    lowered = output.lower()
    return any(
        term in lowered
        for term in (
            "full review comments",
            "review comments",
            "no findings",
            "clean review",
        )
    )


def _plan_hygiene_output_completed(output: str) -> bool:
    return any(_line_is_plan_hygiene_evidence(line) for line in output.splitlines())


def _line_is_plan_mode_evidence(line: str) -> bool:
    command = _command_from_text_line(line)
    if _looks_like_shell_command(line, command):
        return False
    lowered = line.lower()
    return any(term in lowered for term in SESSION_PLAN_TERMS)


def _parse_timestamp_ms(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        numeric = float(value)
        return int(numeric if numeric > 100_000_000_000 else numeric * 1000)
    if not isinstance(value, str):
        return 0
    stripped = value.strip()
    if not stripped:
        return 0
    try:
        numeric = float(stripped)
        return int(numeric if numeric > 100_000_000_000 else numeric * 1000)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _row_timestamp_ms(row: dict[str, object]) -> int:
    timestamp = _parse_timestamp_ms(row.get("timestamp"))
    if timestamp > 0:
        return timestamp
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return 0
    for key in ("timestamp", "created_at_ms", "created_at", "completed_at"):
        timestamp = _parse_timestamp_ms(payload.get(key))
        if timestamp > 0:
            return timestamp
    return 0


def _timestamp_header_values(lines: list[str], header_name: str) -> tuple[int, ...]:
    prefix = f"{header_name}="
    values: list[int] = []
    for line in lines[:25]:
        if not line.startswith(prefix):
            continue
        for value in line.split("=", 1)[1].split(","):
            timestamp = _parse_timestamp_ms(value)
            if timestamp > 0:
                values.append(timestamp)
    return tuple(values)


def _observed_at_ms_from_lines(lines: list[str]) -> int:
    values = _timestamp_header_values(lines, "session_mtime_ms")
    return values[0] if values else 0


def _session_source_from_lines(lines: list[str]) -> str:
    for line in lines[:10]:
        if line.startswith("session_source="):
            return line.split("=", 1)[1].strip()
    return ""


def _is_uncommitted_review_text(text: str) -> bool:
    return any("uncommitted" in line.lower() for line in _review_evidence_lines(text))


def extract_session_signals(session_text_or_path: str) -> SessionSignals:
    candidate_path = Path(session_text_or_path).expanduser()
    observed_at_ms = 0
    if candidate_path.exists():
        observed_at_ms = int(candidate_path.stat().st_mtime * 1000)
        extracted_rollout = (
            extract_codex_rollout_text(candidate_path)
            if candidate_path.suffix == ".jsonl" and candidate_path.name.startswith("rollout-")
            else None
        )
        if extracted_rollout is not None:
            text = extracted_rollout[1]
        elif candidate_path.suffix == ".jsonl":
            text = extract_claude_transcript_text(candidate_path)
        else:
            text = candidate_path.read_text(encoding="utf-8", errors="replace")
        first_line = text.splitlines()[0].strip() if text.splitlines() else ""
        if first_line.lower().startswith("session:"):
            session_id = first_line.split(":", 1)[1].strip() or candidate_path.stem
        else:
            session_id = candidate_path.stem
    else:
        text = session_text_or_path
        lines = text.splitlines()
        observed_at_ms = _observed_at_ms_from_lines(lines)
        first_line = lines[0].strip() if lines else ""
        if first_line.lower().startswith("session:"):
            session_id = first_line.split(":", 1)[1].strip()
        else:
            session_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        if not session_id:
            session_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    lines = text.splitlines()
    header_observed_at_ms = _observed_at_ms_from_lines(lines)
    if header_observed_at_ms > 0:
        observed_at_ms = header_observed_at_ms
    review_lines = _review_evidence_lines(text)
    has_codex_review = bool(review_lines)
    has_uncommitted_review = any("uncommitted" in line.lower() for line in review_lines)
    review_text = _review_reference_text(text) if has_codex_review else ""
    uncommitted_reviewed_at_ms = _timestamp_header_values(lines, "uncommitted_review_event_ms")
    if (
        has_uncommitted_review
        and not uncommitted_reviewed_at_ms
        and _session_source_from_lines(lines) != "codex_rollout"
        and observed_at_ms > 0
    ):
        uncommitted_reviewed_at_ms = (observed_at_ms,)
    return SessionSignals(
        session_id=session_id,
        observed_at_ms=observed_at_ms,
        has_codex_review=has_codex_review,
        has_uncommitted_review=has_uncommitted_review,
        uncommitted_reviewed_at_ms=uncommitted_reviewed_at_ms,
        has_plan_hygiene=any(_line_is_plan_hygiene_evidence(line) for line in lines),
        has_plan_mode=any(_line_is_plan_mode_evidence(line) for line in lines),
        review_refs={match.group(0).lower() for match in HEX_RE.finditer(review_text)}
        if has_codex_review
        else set(),
        text=text,
    )


def claude_signal_text(row: dict[str, object]) -> str:
    if str(row.get("type", "")).lower() != "assistant":
        return ""
    parts: list[str] = []
    text = claude_message_text(row).strip()
    if text:
        parts.append(text)
    message = row.get("message")
    if not isinstance(message, dict):
        return "\n".join(parts)
    content = message.get("content")
    if not isinstance(content, list):
        return "\n".join(parts)
    for item in content:
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "")).lower() != "tool_use":
            continue
        name = str(item.get("name", "")).strip()
        if name:
            parts.append(f"tool_use:{name}")
        tool_input = item.get("input")
        if isinstance(tool_input, dict):
            command = str(tool_input.get("command") or tool_input.get("cmd") or "").strip()
            if command and (
                _is_actual_codex_review_command(command)
                or _is_actual_plan_hygiene_command(command)
            ):
                parts.append(json.dumps({"cmd": audit_redact(command)}, sort_keys=True))
    return "\n".join(parts)


def claude_tool_use_commands(row: dict[str, object]) -> dict[str, str]:
    if str(row.get("type", "")).lower() != "assistant":
        return {}
    message = row.get("message")
    if not isinstance(message, dict):
        return {}
    content = message.get("content")
    if not isinstance(content, list):
        return {}
    commands: dict[str, str] = {}
    for item in content:
        if not isinstance(item, dict) or str(item.get("type", "")).lower() != "tool_use":
            continue
        tool_input = item.get("input")
        if not isinstance(tool_input, dict):
            continue
        command = str(tool_input.get("command") or tool_input.get("cmd") or "").strip()
        if not command:
            continue
        tool_id = str(item.get("id") or item.get("tool_use_id") or item.get("name") or len(commands))
        commands[tool_id] = command
    return commands


def _tool_result_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def claude_tool_result_texts(row: dict[str, object]) -> list[ClaudeToolResult]:
    if str(row.get("type", "")).lower() != "user":
        return []
    message = row.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    results: list[ClaudeToolResult] = []
    for item in content:
        if not isinstance(item, dict) or str(item.get("type", "")).lower() != "tool_result":
            continue
        explicit_id = item.get("tool_use_id") or item.get("id")
        has_explicit_id = explicit_id is not None and str(explicit_id).strip() != ""
        tool_id = str(explicit_id).strip() if has_explicit_id else ""
        text = _tool_result_content_text(item.get("content"))
        if text.strip():
            results.append(
                ClaudeToolResult(
                    tool_id=tool_id,
                    text=text,
                    has_explicit_id=has_explicit_id,
                )
            )
    return results


def extract_claude_transcript_text(path: Path) -> str:
    rows = parse_jsonl(path)
    texts: list[str] = []
    review_event_ms_values: list[int] = []
    uncommitted_review_event_ms_values: list[int] = []
    pending_review_commands: dict[str, str] = {}
    pending_plan_hygiene_commands: dict[str, str] = {}
    for row in rows:
        for tool_id, command in claude_tool_use_commands(row).items():
            if _is_actual_codex_review_command(command):
                pending_review_commands[tool_id] = command
            if _is_actual_plan_hygiene_command(command):
                pending_plan_hygiene_commands[tool_id] = command
        for result in claude_tool_result_texts(row):
            review_command = pending_review_commands.get(result.tool_id)
            if (
                review_command is None
                and not result.has_explicit_id
                and len(pending_review_commands) == 1
            ):
                review_command = next(iter(pending_review_commands.values()))
            if review_command and _review_output_completed(result.text):
                command = _actual_command_step(
                    review_command,
                    ("codex review",),
                ) or review_command
                completed_line = f"{audit_redact(command)} completed"
                texts.append(completed_line)
                reference_text = _review_output_reference_text(result.text)
                if reference_text:
                    texts.append(reference_text)
                timestamp = _row_timestamp_ms(row)
                if timestamp > 0:
                    review_event_ms_values.append(timestamp)
                    if "uncommitted" in completed_line.lower():
                        uncommitted_review_event_ms_values.append(timestamp)
            plan_command = pending_plan_hygiene_commands.get(result.tool_id)
            if (
                plan_command is None
                and not result.has_explicit_id
                and len(pending_plan_hygiene_commands) == 1
            ):
                plan_command = next(iter(pending_plan_hygiene_commands.values()))
            if plan_command and _plan_hygiene_output_completed(result.text):
                texts.append("pre-flight verdict completed by plan-hygiene")
        text = claude_signal_text(row)
        if not text.strip():
            continue
        texts.append(text)
        if _is_uncommitted_review_text(text):
            timestamp = _row_timestamp_ms(row)
            if timestamp > 0:
                review_event_ms_values.append(timestamp)
                uncommitted_review_event_ms_values.append(timestamp)
    text = "\n".join(item for item in texts if item.strip())
    header = [
        f"session:{path.stem}",
        "session_source=claude_transcript",
        f"session_mtime_ms={int(path.stat().st_mtime * 1000)}",
        f"project_dir={path.parent.name}",
    ]
    header.extend(f"review_event_ms={value}" for value in review_event_ms_values)
    header.extend(f"uncommitted_review_event_ms={value}" for value in uncommitted_review_event_ms_values)
    return "\n".join(header + ([text] if text.strip() else []))


def _rollout_text_values(value: object, key: str = "") -> list[str]:
    if key in CODEX_ROLLOUT_SKIP_KEYS:
        return []
    if isinstance(value, str):
        if key and key not in CODEX_ROLLOUT_TEXT_KEYS:
            return []
        text = value.strip()
        return [text[:MAX_CODEX_ROLLOUT_TEXT_VALUE]] if text else []
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(_rollout_text_values(item, key))
        return parts
    if isinstance(value, dict):
        parts = []
        for child_key, child_value in value.items():
            if str(child_key) in CODEX_ROLLOUT_SKIP_KEYS:
                continue
            parts.extend(_rollout_text_values(child_value, str(child_key)))
        return parts
    return []


def _function_call_command(payload: dict[str, object]) -> str:
    arguments = payload.get("arguments")
    if not isinstance(arguments, str):
        return ""
    return _command_from_text_line(arguments)


def _codex_review_mode_completed(payload: dict[str, object]) -> bool:
    return str(payload.get("type", "")).lower() == "exited_review_mode" and isinstance(
        payload.get("review_output"),
        dict,
    )


def extract_codex_rollout_text(path: Path) -> tuple[float, str] | None:
    rows = parse_jsonl(path)
    if not rows:
        return None

    session_id = path.stem
    cwd = ""
    parts: list[str] = []
    review_event_ms_values: list[int] = []
    uncommitted_review_event_ms_values: list[int] = []
    pending_review_commands: dict[str, str] = {}
    pending_plan_hygiene_commands: dict[str, str] = {}
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        row_type = str(row.get("type", ""))
        if row_type == "session_meta":
            session_id = str(payload.get("id") or session_id)
            cwd = str(payload.get("cwd") or cwd)
            continue
        if row_type == "event_msg":
            if _codex_review_mode_completed(payload):
                completed_line = "codex review --uncommitted completed"
                parts.append(completed_line)
                timestamp = _row_timestamp_ms(row)
                if timestamp > 0:
                    review_event_ms_values.append(timestamp)
                    uncommitted_review_event_ms_values.append(timestamp)
            elif str(payload.get("type", "")).lower() == "user_message":
                message = str(payload.get("message") or "").strip()
                if message:
                    parts.append(audit_redact(message))
            continue
        if row_type != "response_item":
            continue
        payload_type = str(payload.get("type", "")).lower()
        call_id = str(payload.get("call_id") or "")
        if payload_type == "function_call":
            command = _function_call_command(payload)
            if call_id and _is_actual_codex_review_command(command):
                pending_review_commands[call_id] = command
            if call_id and _is_actual_plan_hygiene_command(command):
                pending_plan_hygiene_commands[call_id] = command
        if payload_type == "function_call_output":
            output = str(payload.get("output") or "")
            if call_id in pending_review_commands and _review_output_completed(output):
                command = _actual_command_step(
                    pending_review_commands[call_id],
                    ("codex review",),
                ) or pending_review_commands[call_id]
                command = audit_redact(command)
                row_parts = [f"{command} completed"]
                reference_text = _review_output_reference_text(output)
                if reference_text:
                    row_parts.append(reference_text)
                timestamp = _row_timestamp_ms(row)
                if timestamp > 0:
                    review_event_ms_values.append(timestamp)
                    if "uncommitted" in command.lower():
                        uncommitted_review_event_ms_values.append(timestamp)
                parts.extend(row_parts)
            if call_id in pending_plan_hygiene_commands and _plan_hygiene_output_completed(output):
                parts.append("pre-flight verdict completed by plan-hygiene")
            continue
        if payload_type == "message":
            role = str(payload.get("role", "")).lower()
            if role not in {"assistant", "user"}:
                continue
        if payload_type not in {"function_call", "message"}:
            continue
        row_parts = _rollout_text_values(payload)
        row_text = "\n".join(row_parts)
        if _is_uncommitted_review_text(row_text):
            timestamp = _row_timestamp_ms(row)
            if timestamp > 0:
                review_event_ms_values.append(timestamp)
                uncommitted_review_event_ms_values.append(timestamp)
        parts.extend(row_parts)

    header = [
        f"session:{session_id}",
        "session_source=codex_rollout",
        f"session_mtime_ms={int(path.stat().st_mtime * 1000)}",
    ]
    header.extend(f"review_event_ms={value}" for value in review_event_ms_values)
    header.extend(f"uncommitted_review_event_ms={value}" for value in uncommitted_review_event_ms_values)
    if cwd:
        header.append(f"cwd={cwd}")
    text = "\n".join(header + parts)
    if not text.strip():
        return None
    return (path.stat().st_mtime, text)


def _episode_tags(episode: dict[str, object]) -> list[str]:
    tags = episode.get("tags", [])
    if not isinstance(tags, list):
        return []
    return [str(tag) for tag in tags]


def is_audit_episode(episode: dict[str, object]) -> bool:
    return episode.get("project_slug") == AUDIT_PROJECT_SLUG or AUDIT_TAG in _episode_tags(episode)


def is_run_snapshot(episode: dict[str, object]) -> bool:
    audit = episode.get("audit")
    return (
        AUDIT_RUN_TAG in _episode_tags(episode)
        or (isinstance(audit, dict) and audit.get("kind") == "run_snapshot")
    )


def commits_with_memory_capture(episodes: list[dict[str, object]]) -> set[str]:
    refs: set[str] = set()
    for episode in episodes:
        if is_audit_episode(episode):
            continue
        text = compact_json(episode)
        refs.update(match.group(0).lower() for match in HEX_RE.finditer(text))
    return refs


def files_with_memory_capture(episodes: list[dict[str, object]]) -> set[str]:
    return files_with_memory_capture_for_repo(episodes, None)


def _episode_created_at_ms(episode: dict[str, object]) -> int:
    try:
        return int(episode.get("created_at_ms", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _episode_matches_repo(episode: dict[str, object], repo_root: str | None) -> bool:
    if repo_root is None:
        return True
    root = _normalize_path_ref(repo_root)
    project_path = _normalize_path_ref(str(episode.get("project_path", "")))
    project_slug = str(episode.get("project_slug", "")).strip().lower()
    repo_name = Path(repo_root).name.lower()
    if project_path:
        return project_path == root or project_path.startswith(f"{root}/")
    return project_slug == repo_name or project_slug.replace("/", "-") == repo_name


def files_with_memory_capture_for_repo(
    episodes: list[dict[str, object]],
    repo_root: str | None,
) -> set[str]:
    return set(files_with_memory_capture_times_for_repo(episodes, repo_root))


def files_with_memory_capture_times_for_repo(
    episodes: list[dict[str, object]],
    repo_root: str | None,
) -> dict[str, int]:
    paths: dict[str, int] = {}
    for episode in episodes:
        if is_audit_episode(episode):
            continue
        if not _episode_matches_repo(episode, repo_root):
            continue
        files = episode.get("files_touched", [])
        if not isinstance(files, list):
            continue
        captured_at = _episode_created_at_ms(episode)
        for file_path in files:
            normalized = _captured_file_path_for_repo(episode, repo_root, str(file_path))
            if normalized:
                paths[normalized] = max(paths.get(normalized, 0), captured_at)
    return paths


def completion_hygiene_refs(episodes: list[dict[str, object]]) -> set[str]:
    refs: set[str] = set()
    for episode in episodes:
        if is_audit_episode(episode):
            continue
        text = compact_json(episode)
        lowered = text.lower()
        if (
            "completion-hygiene" not in lowered
            and "completion audit" not in lowered
            and "completion-audit" not in lowered
        ):
            continue
        refs.update(match.group(0).lower() for match in HEX_RE.finditer(text))
    return refs


def artifact_refs_from_repo(repo_root: str, prefixes: tuple[str, ...]) -> set[str]:
    refs: set[str] = set()
    root = Path(repo_root)
    for prefix in prefixes:
        base = root / prefix
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            refs.update(match.group(0).lower() for match in HEX_RE.finditer(text))
    return refs


def artifact_session_refs_from_repo(repo_root: str, prefixes: tuple[str, ...]) -> set[str]:
    refs: set[str] = set()
    root = Path(repo_root)
    for prefix in prefixes:
        base = root / prefix
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in SESSION_REF_RE.finditer(text):
                ref = match.group(1).strip("`'\".,;:)]}").lower()
                if ref:
                    refs.add(ref)
    return refs


def _normalize_path_ref(path: str) -> str:
    normalized = path.strip().replace("\\", "/").lower()
    if normalized.startswith(("/private/var/", "/private/tmp/")):
        normalized = normalized.removeprefix("/private")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _path_ref_matches(changed_path: str, captured_path: str) -> bool:
    changed = _normalize_path_ref(changed_path)
    captured = _normalize_path_ref(captured_path)
    if not changed or not captured:
        return False
    return changed == captured


def _captured_file_path_for_repo(
    episode: dict[str, object],
    repo_root: str | None,
    file_path: str,
) -> str:
    normalized = _normalize_path_ref(file_path)
    if not normalized:
        return ""
    if repo_root is None:
        return normalized

    root = _normalize_path_ref(repo_root)
    if root and normalized.startswith(f"{root}/"):
        return normalized[len(root) + 1 :]
    if normalized.startswith("/"):
        return normalized

    project_path = _normalize_path_ref(str(episode.get("project_path", "")))
    if not root or not project_path or not project_path.startswith(f"{root}/"):
        return normalized
    project_relative = project_path[len(root) + 1 :]
    if not project_relative or normalized == project_relative or normalized.startswith(f"{project_relative}/"):
        return normalized
    return f"{project_relative}/{normalized}"


def _commit_files_match_capture(commit: GitCommit, captured_files: set[str] | dict[str, int]) -> bool:
    if isinstance(captured_files, dict):
        return any(
            _path_ref_matches(changed_path, captured_path)
            and (
                commit.committed_at_ms == 0
                or captured_at >= commit.committed_at_ms
            )
            for changed_path in commit.changed_files
            for captured_path, captured_at in captured_files.items()
        )
    return any(
        _path_ref_matches(changed_path, captured_path)
        for changed_path in commit.changed_files
        for captured_path in captured_files
    )


def _text_contains_path_token(text: str, token: str) -> bool:
    token = token.strip().lower().rstrip("/")
    if not token:
        return False
    start = 0
    boundary_chars = set("/= \n\t\r\"'`:;,()[]{}")
    while True:
        index = text.find(token, start)
        if index < 0:
            return False
        end = index + len(token)
        starts_at_boundary = index == 0 or text[index - 1] in boundary_chars
        ends_at_boundary = end == len(text) or text[end] in boundary_chars
        if starts_at_boundary and ends_at_boundary:
            return True
        start = index + 1


def _path_text_variants(path: str) -> set[str]:
    normalized = path.strip().lower().rstrip("/")
    if not normalized:
        return set()
    variants = {normalized}
    if normalized.startswith("/private/"):
        variants.add(normalized.removeprefix("/private"))
    elif normalized.startswith("/"):
        variants.add(f"/private{normalized}")
    return variants


def _ref_matches(commit_sha: str, refs: set[str]) -> bool:
    sha = commit_sha.lower()
    return any(sha == ref or sha.startswith(ref) or ref.startswith(sha) for ref in refs)


def _tag_value(tags: list[str], prefix: str) -> str:
    for tag in tags:
        if tag.startswith(prefix):
            return tag.split(":", 1)[1]
    return ""


def _status_of(episode: dict[str, object]) -> str:
    audit = episode.get("audit")
    if isinstance(audit, dict) and audit.get("status"):
        return str(audit["status"])
    return _tag_value(_episode_tags(episode), "status:") or STATUS_OPEN


def _confidence_of(episode: dict[str, object]) -> str:
    audit = episode.get("audit")
    if isinstance(audit, dict) and audit.get("confidence"):
        return str(audit["confidence"])
    return _tag_value(_episode_tags(episode), "confidence:") or "unknown"


def _gate_of(episode: dict[str, object]) -> str:
    audit = episode.get("audit")
    if isinstance(audit, dict) and audit.get("gate_id"):
        return str(audit["gate_id"])
    return _tag_value(_episode_tags(episode), "gate:") or "unknown"


def _baseline_reason(raw: object) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        reason = raw.get("reason") or raw.get("note") or raw.get("accepted_reason")
        if reason:
            return str(reason).strip()
    return "accepted historical baseline"


def load_baseline_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AgentMemoryDoctrineAuditError(f"{path}: invalid baseline JSON: {exc}") from exc
    except OSError as exc:
        raise AgentMemoryDoctrineAuditError(f"{path}: cannot read baseline file: {exc}") from exc
    if not isinstance(payload, dict):
        raise AgentMemoryDoctrineAuditError(f"{path}: baseline root must be an object")
    raw_findings = (
        payload.get("waived_findings")
        or payload.get("accepted_findings")
        or payload.get("findings")
        or {}
    )
    baseline: dict[str, str] = {}
    if isinstance(raw_findings, dict):
        for episode_id, config in raw_findings.items():
            key = str(episode_id).strip()
            if key:
                baseline[key] = _baseline_reason(config)
        return baseline
    if isinstance(raw_findings, list):
        for item in raw_findings:
            if isinstance(item, str):
                key = item.strip()
                if key:
                    baseline[key] = "accepted historical baseline"
                continue
            if isinstance(item, dict):
                key = str(item.get("episode_id") or item.get("id") or "").strip()
                if key:
                    baseline[key] = _baseline_reason(item)
                continue
            raise AgentMemoryDoctrineAuditError(f"{path}: invalid baseline finding entry")
        return baseline
    raise AgentMemoryDoctrineAuditError(f"{path}: baseline findings must be an object or array")


def load_baseline_findings(
    repo_roots: list[str],
    baseline_file: str | None,
) -> dict[str, str]:
    if baseline_file:
        return load_baseline_file(Path(baseline_file).expanduser().resolve())
    baseline: dict[str, str] = {}
    for repo_root in repo_roots:
        baseline.update(load_baseline_file(Path(repo_root) / DEFAULT_BASELINE_FILE))
    return baseline


def build_finding_payload(
    finding: Finding,
    prior_by_id: dict[str, dict[str, object]],
    created_at_ms: int,
    run: str,
    status: str = STATUS_OPEN,
    waiver_reason: str | None = None,
) -> dict[str, object]:
    episode_id = finding_id(finding.repo_id, finding.gate_id, finding.offending_ref)
    prior = prior_by_id.get(episode_id, {})
    first_seen = int(prior.get("created_at_ms", created_at_ms))
    evidence = [audit_redact(item) for item in finding.evidence]
    summary = audit_redact(finding.summary)
    tags = [
        AUDIT_TAG,
        f"repo:{finding.repo_id}",
        f"gate:{finding.gate_id}",
        f"status:{status}",
        f"confidence:{finding.confidence}",
        f"run:{run}",
    ]
    if status == STATUS_WAIVED:
        tags.append("waiver:baseline")
    next_steps = ["Review the evidence, fix the doctrine gap, then rerun the audit."]
    if status == STATUS_WAIVED:
        next_steps = ["Accepted as repo-local historical baseline; no remediation required unless it recurs."]
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "episode_id": episode_id,
        "created_at_ms": first_seen,
        "scope": AUDIT_SCOPE,
        "project_path": finding.repo_root,
        "project_slug": AUDIT_PROJECT_SLUG,
        "source": AUDIT_SOURCE,
        "summary": summary,
        "decisions": [],
        "supersedes": [],
        "files_touched": finding.files_touched,
        "commands": [],
        "verification": evidence,
        "open_questions": [],
        "risks": [f"{finding.confidence}:{finding.gate_id}"],
        "next_steps": next_steps,
        "tags": tags,
        "audit": {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "repo_id": finding.repo_id,
            "repo_root": finding.repo_root,
            "gate_id": finding.gate_id,
            "offending_ref": finding.offending_ref,
            "confidence": finding.confidence,
            "status": status,
            "run_id": run,
            "first_seen_at_ms": first_seen,
            "updated_at_ms": created_at_ms,
            "evidence": evidence,
        },
    }
    if waiver_reason:
        payload["audit"]["waiver_reason"] = audit_redact(waiver_reason)
    validate_episode_payload(payload)
    return payload


def build_run_snapshot_payload(
    run: str,
    created_at_ms: int,
    repo_roots: list[str],
    counts: dict[str, object],
    digest: str,
    finding_payloads: list[dict[str, object]],
) -> dict[str, object]:
    summary = f"Doctrine audit run {run}: {counts.get('findings', 0)} finding(s)."
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "episode_id": f"da-run-{run}",
        "created_at_ms": created_at_ms,
        "scope": AUDIT_SCOPE,
        "project_path": repo_roots[0] if repo_roots else "",
        "project_slug": AUDIT_PROJECT_SLUG,
        "source": AUDIT_SOURCE,
        "summary": summary,
        "decisions": [],
        "supersedes": [],
        "files_touched": [],
        "commands": [],
        "verification": [
            f"run:{run}",
            f"findings:{counts.get('findings', 0)}",
            "digest:persisted",
        ],
        "open_questions": [],
        "risks": [],
        "next_steps": ["Review digest findings, remediate gaps, then rerun the audit."],
        "tags": [AUDIT_TAG, AUDIT_RUN_TAG, f"run:{run}"],
        "audit": {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "kind": "run_snapshot",
            "run_id": run,
            "repo_roots": repo_roots,
            "counts": counts,
            "digest": digest,
            "finding_ids": [str(finding_payload["episode_id"]) for finding_payload in finding_payloads],
        },
    }
    validate_episode_payload(payload)
    return payload


def mark_resolved_payload(
    prior: dict[str, object],
    updated_at_ms: int,
    run: str,
) -> dict[str, object]:
    payload = dict(prior)
    tags = [
        tag for tag in _episode_tags(payload)
        if not tag.startswith("status:") and not tag.startswith("run:")
    ]
    tags.extend([f"status:{STATUS_RESOLVED}", f"run:{run}"])
    payload["tags"] = tags
    audit = dict(payload.get("audit") if isinstance(payload.get("audit"), dict) else {})
    audit["status"] = STATUS_RESOLVED
    audit["run_id"] = run
    audit["updated_at_ms"] = updated_at_ms
    payload["audit"] = audit
    validate_episode_payload(payload)
    return payload


def carry_open_payload(
    prior: dict[str, object],
    updated_at_ms: int,
    run: str,
) -> dict[str, object]:
    payload = dict(prior)
    tags = [
        tag for tag in _episode_tags(payload)
        if not tag.startswith("status:") and not tag.startswith("run:")
    ]
    tags.extend([f"status:{STATUS_OPEN}", f"run:{run}"])
    payload["tags"] = tags
    audit = dict(payload.get("audit") if isinstance(payload.get("audit"), dict) else {})
    audit["status"] = STATUS_OPEN
    audit["run_id"] = run
    audit["updated_at_ms"] = updated_at_ms
    payload["audit"] = audit
    validate_episode_payload(payload)
    return payload


def mark_waived_payload(
    prior: dict[str, object],
    updated_at_ms: int,
    run: str,
    waiver_reason: str,
) -> dict[str, object]:
    payload = dict(prior)
    tags = [
        tag for tag in _episode_tags(payload)
        if not tag.startswith("status:") and not tag.startswith("run:") and tag != "waiver:baseline"
    ]
    tags.extend([f"status:{STATUS_WAIVED}", f"run:{run}", "waiver:baseline"])
    payload["tags"] = tags
    payload["next_steps"] = ["Accepted as repo-local historical baseline; no remediation required unless it recurs."]
    audit = dict(payload.get("audit") if isinstance(payload.get("audit"), dict) else {})
    audit["status"] = STATUS_WAIVED
    audit["run_id"] = run
    audit["updated_at_ms"] = updated_at_ms
    audit["waiver_reason"] = audit_redact(waiver_reason)
    payload["audit"] = audit
    validate_episode_payload(payload)
    return payload


def plan_lifecycle_payloads(
    findings: list[Finding],
    prior_audit_episodes: list[dict[str, object]],
    created_at_ms: int,
    run: str,
    current_refs: set[str] | None = None,
    baseline_by_id: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    prior_by_id = {str(episode.get("episode_id")): episode for episode in prior_audit_episodes}
    baseline = baseline_by_id or {}
    current_payloads: list[dict[str, object]] = []
    for finding in findings:
        episode_id = finding_id(finding.repo_id, finding.gate_id, finding.offending_ref)
        status = STATUS_WAIVED if episode_id in baseline else STATUS_OPEN
        current_payloads.append(
            build_finding_payload(
                finding,
                prior_by_id,
                created_at_ms,
                run,
                status,
                baseline.get(episode_id),
            )
        )
    current_ids = {str(payload["episode_id"]) for payload in current_payloads}
    lifecycle_payloads: list[dict[str, object]] = []
    for prior_id, prior in prior_by_id.items():
        if prior_id in current_ids or _status_of(prior) != STATUS_OPEN:
            continue
        if prior_id in baseline:
            lifecycle_payloads.append(
                mark_waived_payload(prior, created_at_ms, run, baseline[prior_id])
            )
            continue
        audit = prior.get("audit") if isinstance(prior.get("audit"), dict) else {}
        offending_ref = str(audit.get("offending_ref", ""))
        if current_refs is not None and offending_ref in current_refs:
            lifecycle_payloads.append(mark_resolved_payload(prior, created_at_ms, run))
        else:
            lifecycle_payloads.append(carry_open_payload(prior, created_at_ms, run))
    return current_payloads + lifecycle_payloads


def _audit_repo_id(episode: dict[str, object]) -> str:
    audit = episode.get("audit")
    if isinstance(audit, dict) and audit.get("repo_id"):
        return str(audit["repo_id"])
    return _tag_value(_episode_tags(episode), "repo:")


def filter_prior_for_repos(
    prior_audit_episodes: list[dict[str, object]],
    repo_ids: set[str],
) -> list[dict[str, object]]:
    return [
        episode for episode in prior_audit_episodes
        if _audit_repo_id(episode) in repo_ids
    ]


def _stamp_repo(raw_findings: list[Finding], repo_id: str, repo_root: str) -> list[Finding]:
    return [
        Finding(
            gate_id=finding.gate_id,
            repo_id=repo_id,
            repo_root=repo_root,
            offending_ref=finding.offending_ref,
            confidence=finding.confidence,
            summary=finding.summary,
            evidence=finding.evidence,
            files_touched=finding.files_touched,
        )
        for finding in raw_findings
    ]


def session_matches_repo(signal: SessionSignals, repo_root: str, repo_count: int) -> bool:
    if session_text_matches_repo(signal.text, repo_root):
        return True
    if repo_count == 1 and not _has_repo_context_marker(signal.text):
        return True
    return False


def _has_repo_context_marker(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "cwd=",
            "workdir=",
            "working_dir=",
            "project_path=",
            "repo_root=",
            "project=",
            "repo=",
        )
    )


def session_text_matches_any_repo(text: str, repo_roots: list[str] | None) -> bool:
    if not repo_roots:
        return True
    return any(session_text_matches_repo(text, repo_root) for repo_root in repo_roots)


def session_text_matches_repo(text: str, repo_root: str) -> bool:
    lowered = text.lower()
    root = repo_root.lower()
    name = Path(repo_root).name.lower()
    root_variants = _path_text_variants(root)
    encoded_variants = {variant.strip("/").replace("/", "-") for variant in root_variants}
    encoded_variants.update(f"-{variant}" for variant in list(encoded_variants) if variant)
    return (
        any(_text_contains_path_token(lowered, variant) for variant in root_variants)
        or any(_text_contains_path_token(lowered, variant) for variant in encoded_variants)
        or any(_text_contains_path_token(lowered, f"cwd={variant}") for variant in root_variants)
        or any(_text_contains_path_token(lowered, f"workdir={variant}") for variant in root_variants)
        or any(_text_contains_path_token(lowered, f"working_dir={variant}") for variant in root_variants)
        or any(_text_contains_path_token(lowered, f"project_path={variant}") for variant in root_variants)
        or any(_text_contains_path_token(lowered, f"repo_root={variant}") for variant in root_variants)
        or _text_contains_path_token(lowered, f"project={name}")
        or _text_contains_path_token(lowered, f"repo={name}")
    )


def session_has_audit_signal(text: str) -> bool:
    signal = extract_session_signals(text)
    return (
        signal.has_codex_review
        or signal.has_uncommitted_review
        or signal.has_plan_hygiene
        or signal.has_plan_mode
    )


def collect_findings_with_refs(
    repo_roots: list[str],
    since_days: int,
    sessions: list[str],
    memory_episodes: list[dict[str, object]],
) -> tuple[list[Finding], set[str]]:
    repo_tops = unique_git_toplevels(repo_roots)
    session_signals = [extract_session_signals(session) for session in sessions]
    capture_refs = commits_with_memory_capture(memory_episodes)
    completion_refs = completion_hygiene_refs(memory_episodes)
    reviewed_refs = {
        ref for signal in session_signals for ref in signal.review_refs
    }
    findings: list[Finding] = []
    current_refs = {f"session:{signal.session_id}" for signal in session_signals}
    repo_count = len(repo_tops)
    for repo_root in repo_tops:
        rid = repo_id_for(repo_root)
        commits = read_git_window(repo_root, since_days)
        capture_files = files_with_memory_capture_times_for_repo(memory_episodes, repo_root)
        current_refs.update(commit.sha for commit in commits)
        plan_hygiene_session_refs = artifact_session_refs_from_repo(
            repo_root,
            PLAN_HYGIENE_ARTIFACT_PREFIXES,
        )
        repo_completion_refs = completion_refs | artifact_refs_from_repo(
            repo_root,
            COMPLETION_ARTIFACT_PREFIXES,
        )
        raw: list[Finding] = []
        raw.extend(detect_completion_review(commits, repo_completion_refs))
        raw.extend(
            detect_risk_review(
                commits,
                reviewed_refs,
                uncommitted_reviewed_at_ms=[
                    reviewed_at_ms
                    for signal in session_signals
                    if signal.has_uncommitted_review
                    and session_matches_repo(signal, repo_root, repo_count)
                    for reviewed_at_ms in signal.uncommitted_reviewed_at_ms
                ],
            )
        )
        raw.extend(detect_decision_capture(commits, capture_refs, capture_files))
        raw.extend(
            finding
            for signal in session_signals
            if session_matches_repo(signal, repo_root, repo_count)
            for finding in detect_plan_review(
                signal,
                rid,
                repo_root,
                plan_hygiene_session_refs,
            )
        )
        findings.extend(_stamp_repo(raw, rid, repo_root))
    return findings, current_refs


def collect_findings(
    repo_roots: list[str],
    since_days: int,
    sessions: list[str],
    memory_episodes: list[dict[str, object]],
) -> list[Finding]:
    findings, _current_refs = collect_findings_with_refs(
        repo_roots,
        since_days,
        sessions,
        memory_episodes,
    )
    return findings


def discover_session_texts(
    home: Path,
    limit: int = DEFAULT_AUTO_SESSION_LIMIT,
    repo_roots: list[str] | None = None,
    min_mtime: float | None = None,
) -> list[str]:
    sessions: list[tuple[float, str]] = []
    codex_sessions_root = home / ".codex" / "sessions"
    if codex_sessions_root.exists():
        paths = sorted(
            codex_sessions_root.rglob("rollout-*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in paths:
            if min_mtime is not None and path.stat().st_mtime < min_mtime:
                continue
            extracted = extract_codex_rollout_text(path)
            if extracted is not None:
                if (
                    session_text_matches_any_repo(extracted[1], repo_roots)
                    and session_has_audit_signal(extracted[1])
                ):
                    sessions.append(extracted)

    claude_root = home / ".claude" / "projects"
    if claude_root.exists():
        paths = sorted(
            claude_root.glob("*/*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in paths:
            if min_mtime is not None and path.stat().st_mtime < min_mtime:
                continue
            text = extract_claude_transcript_text(path)
            if len(text.splitlines()) <= 4:
                continue
            if session_text_matches_any_repo(text, repo_roots) and session_has_audit_signal(text):
                sessions.append((path.stat().st_mtime, text))

    sessions.sort(key=lambda item: item[0], reverse=True)
    return [text for _timestamp, text in sessions[:limit]]


def _counts(payloads: list[dict[str, object]]) -> dict[str, object]:
    by_status = Counter(_status_of(payload) for payload in payloads)
    by_confidence = Counter(_confidence_of(payload) for payload in payloads)
    by_gate = Counter(_gate_of(payload) for payload in payloads)
    return {
        "findings": len(payloads),
        "by_status": dict(sorted(by_status.items())),
        "by_confidence": dict(sorted(by_confidence.items())),
        "by_gate": dict(sorted(by_gate.items())),
    }


def _plan_actions(payloads: list[dict[str, object]], dsn: str) -> list[dict[str, object]]:
    actions = [{"kind": "finding", "payload": payload} for payload in payloads]
    actions.append(
        {
            "kind": "reindex_required",
            "project_slug": AUDIT_PROJECT_SLUG,
            "command": (
                "python3 scripts/agent_memory_embed_index.py index "
                f"--source postgres --project-slug {AUDIT_PROJECT_SLUG} "
                f"--dsn {shlex.quote(dsn)} --reindex --index {DEFAULT_AUDIT_INDEX}"
            ),
        }
    )
    return actions


def upsert_findings(dsn: str, payloads: Sequence[dict[str, object]]) -> int:
    written = 0
    for payload in payloads:
        upsert_episode(dsn, payload)
        written += 1
    return written


def disk_plan_summary(plan: dict[str, object]) -> dict[str, object]:
    summary = {
        key: value for key, value in plan.items()
        if key != "actions"
    }
    actions = plan.get("actions", [])
    if isinstance(actions, list):
        action_counts = Counter(
            str(action.get("kind", "unknown"))
            for action in actions
            if isinstance(action, dict)
        )
        summary["action_counts"] = dict(
            sorted(action_counts.items())
        )
        summary["reindex_commands"] = [
            str(action["command"]) for action in actions
            if isinstance(action, dict)
            and action.get("kind") == "reindex_required"
            and action.get("command")
        ]
    return summary


def render_trend_digest(
    prior: list[dict[str, object]],
    current: list[dict[str, object]],
    as_of: str,
    max_items: int | None = None,
) -> str:
    prior_by_id = {str(episode.get("episode_id")): episode for episode in prior}
    current_by_id = {str(episode.get("episode_id")): episode for episode in current}
    new_ids = sorted(set(current_by_id) - set(prior_by_id))
    resolved_ids = sorted(
        episode_id
        for episode_id, episode in current_by_id.items()
        if _status_of(episode) == STATUS_RESOLVED
        and _status_of(prior_by_id.get(episode_id, {})) != STATUS_RESOLVED
    )
    aged_ids = sorted(set(prior_by_id) - set(current_by_id))
    by_conf = Counter(_confidence_of(episode) for episode in current)
    by_gate = Counter(_gate_of(episode) for episode in current)
    open_items = [episode for episode in current if _status_of(episode) == STATUS_OPEN]
    ranked = sorted(
        open_items,
        key=lambda episode: (
            _confidence_of(episode) != CONF_VIOLATION,
            _gate_of(episode),
            str(episode.get("episode_id")),
        ),
    )
    item_limit = max(1, max_items) if max_items is not None else None
    ranked_display = ranked[:item_limit] if item_limit is not None else ranked
    new_display = new_ids[:item_limit] if item_limit is not None else new_ids
    resolved_display = resolved_ids[:item_limit] if item_limit is not None else resolved_ids
    lines = [
        f"# Doctrine-Audit Digest - {as_of}",
        "",
        (
            f"Total: {len(current)} | open: {len(open_items)} | "
            f"violation: {by_conf.get(CONF_VIOLATION, 0)} | "
            f"advisory: {by_conf.get(CONF_ADVISORY, 0)}"
        ),
        f"New: {len(new_ids)} | Resolved: {len(resolved_ids)} | Aged-out of window: {len(aged_ids)}",
        "",
        "## Gate Counts",
    ]
    if by_gate:
        lines.extend(f"- {gate}: {count}" for gate, count in sorted(by_gate.items()))
    else:
        lines.append("- none")
    lines.extend(["", "## Ranked Findings"])
    if ranked_display:
        for episode in ranked_display:
            audit = episode.get("audit") if isinstance(episode.get("audit"), dict) else {}
            evidence = episode.get("verification", [])
            evidence_text = "; ".join(str(item) for item in evidence[:3]) if isinstance(evidence, list) else ""
            lines.append(
                f"- [{_confidence_of(episode)}] {_gate_of(episode)} "
                f"`{episode.get('episode_id')}` - {str(episode.get('summary', ''))[:180]}"
            )
            if evidence_text:
                lines.append(f"  Evidence: {evidence_text}")
            if audit.get("repo_root"):
                lines.append(f"  Repo: {audit['repo_root']}")
        if len(ranked_display) < len(ranked):
            lines.append(f"- ... {len(ranked) - len(ranked_display)} more open finding(s) omitted by limit")
    else:
        lines.append("- none")
    lines.extend(["", "## New This Run"])
    if new_display:
        lines.extend(f"- {episode_id}" for episode_id in new_display)
        if len(new_display) < len(new_ids):
            lines.append(f"- ... {len(new_ids) - len(new_display)} more new finding(s) omitted by limit")
    else:
        lines.append("- none")
    lines.extend(["", "## Resolved This Run"])
    if resolved_display:
        lines.extend(f"- {episode_id}" for episode_id in resolved_display)
        if len(resolved_display) < len(resolved_ids):
            lines.append(f"- ... {len(resolved_ids) - len(resolved_display)} more resolved finding(s) omitted by limit")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## ROI Frame",
            (
                "This digest protects review discipline by catching missing gates before "
                "they compound into stale memory, unreviewed risk-path edits, or false "
                "completion claims."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def latest_snapshot_digest(episodes: list[dict[str, object]]) -> str | None:
    snapshots = [episode for episode in episodes if is_run_snapshot(episode)]
    def snapshot_run(episode: dict[str, object]) -> str:
        audit = episode.get("audit")
        if isinstance(audit, dict) and audit.get("run_id"):
            return str(audit["run_id"])
        return _tag_value(_episode_tags(episode), "run:")

    snapshots.sort(key=snapshot_run, reverse=True)
    for snapshot in snapshots:
        audit = snapshot.get("audit")
        if isinstance(audit, dict) and audit.get("digest"):
            return str(audit["digest"])
    return None


def select_digest_windows(
    episodes: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    by_run: dict[str, list[dict[str, object]]] = defaultdict(list)
    for episode in episodes:
        audit = episode.get("audit")
        run = ""
        if isinstance(audit, dict):
            run = str(audit.get("run_id", ""))
        if not run:
            run = _tag_value(_episode_tags(episode), "run:")
        by_run[run or "unknown"].append(episode)
    known_run_ids = sorted((run for run in by_run if run != "unknown"), reverse=True)
    run_ids = known_run_ids + (["unknown"] if "unknown" in by_run else [])
    if not run_ids:
        return [], []
    current = by_run[run_ids[0]]
    prior = by_run[run_ids[1]] if len(run_ids) > 1 else []
    return prior, current


def run_audit(
    repo_roots: list[str],
    since_days: int = 7,
    sessions: list[str] | None = None,
    reviewed: bool = False,
    dsn: str | None = None,
    run_root: Path | None = None,
    session_home: Path | None = None,
    auto_session_limit: int = DEFAULT_AUTO_SESSION_LIMIT,
    baseline_file: str | None = None,
    fetch_episodes_fn: FetchEpisodesFn = fetch_episodes,
    upsert_fn: UpsertFindingsFn = upsert_findings,
) -> dict[str, object]:
    if not repo_roots:
        raise AgentMemoryDoctrineAuditError("at least one repo root is required")
    current_dsn = dsn or default_dsn()
    run = run_id()
    created = now_ms()
    output_dir = (run_root or DEFAULT_RUN_ROOT).expanduser().resolve() / run
    output_dir.mkdir(parents=True, exist_ok=True)
    repo_tops = unique_git_toplevels(repo_roots)
    session_min_mtime = time.time() - (max(1, since_days) * 86_400)
    sessions = discover_session_texts(
        (session_home or Path.home()).expanduser(),
        auto_session_limit,
        repo_tops,
        session_min_mtime,
    ) if sessions is None else sessions

    prior_audit = fetch_episodes_fn(current_dsn, [AUDIT_PROJECT_SLUG], None)
    memory_episodes = fetch_episodes_fn(current_dsn, None, None)
    scanned_repo_ids = {repo_id_for(repo) for repo in repo_tops}
    scoped_prior = filter_prior_for_repos(prior_audit, scanned_repo_ids)
    baseline_by_id = load_baseline_findings(repo_tops, baseline_file)
    findings, current_refs = collect_findings_with_refs(
        repo_roots,
        since_days,
        sessions,
        memory_episodes,
    )
    payloads = plan_lifecycle_payloads(
        findings,
        scoped_prior,
        created,
        run,
        current_refs,
        baseline_by_id,
    )
    as_of = time.strftime("%Y-%m-%d")
    _older_digest, prior_digest = select_digest_windows(scoped_prior)
    digest = render_trend_digest(prior=prior_digest, current=payloads, as_of=as_of)
    counts = _counts(payloads)
    snapshot_payload = build_run_snapshot_payload(
        run=run,
        created_at_ms=created,
        repo_roots=repo_tops,
        counts=counts,
        digest=digest,
        finding_payloads=payloads,
    )
    actions = _plan_actions(payloads, current_dsn)
    actions.append({"kind": "run_snapshot", "payload": snapshot_payload})
    plan: dict[str, object] = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "run_id": run,
        "created_at_ms": created,
        "review_required": True,
        "reviewed": reviewed,
        "audit_project_slug": AUDIT_PROJECT_SLUG,
        "repo_roots": repo_tops,
        "since_days": since_days,
        "session_count": len(sessions),
        "run_dir": str(output_dir),
        "counts": counts,
        "digest_path": str(output_dir / "digest.md"),
        "snapshot_episode_id": snapshot_payload["episode_id"],
        "baseline_findings": len(baseline_by_id),
        "applied_findings": 0,
        "applied_run_snapshots": 0,
        "actions": actions,
    }
    (output_dir / "digest.md").write_text(digest, encoding="utf-8")
    if reviewed:
        if payloads:
            plan["applied_findings"] = upsert_fn(current_dsn, payloads)
        plan["applied_run_snapshots"] = upsert_fn(current_dsn, [snapshot_payload])
    write_json(output_dir / "plan.json", disk_plan_summary(plan))
    return plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan and optionally apply daily doctrine-conformance audit findings."
    )
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        help="Repository root to audit. Repeatable; defaults to current directory.",
    )
    parser.add_argument("--since-days", type=int, default=7)
    parser.add_argument(
        "--session",
        action="append",
        default=None,
        help="Session text or path to scan for codex-review / plan-hygiene signals.",
    )
    parser.add_argument("--session-home", default=str(Path.home()))
    parser.add_argument("--auto-session-limit", type=int, default=DEFAULT_AUTO_SESSION_LIMIT)
    parser.add_argument(
        "--reviewed",
        action="store_true",
        help="Write finding episodes to Postgres. Omit for plan-only/digest-only runs.",
    )
    parser.add_argument("--dsn", default=default_dsn())
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument(
        "--baseline-file",
        default=None,
        help=(
            "JSON file of accepted finding IDs. Defaults to "
            "docs/doctrine-audit-baseline.json in each audited repo when present."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        plan = run_audit(
            repo_roots=args.repo or ["."],
            since_days=args.since_days,
            sessions=args.session,
            reviewed=args.reviewed,
            dsn=args.dsn,
            run_root=Path(args.run_root),
            session_home=Path(args.session_home),
            auto_session_limit=args.auto_session_limit,
            baseline_file=args.baseline_file,
        )
    except (
        AgentMemoryDoctrineAuditError,
        AgentMemoryPostgresError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"counts": plan["counts"], "run_dir": plan["run_dir"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
