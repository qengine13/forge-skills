"""Language adapters — run each language's own tools and turn their output into per-file counts.

Each adapter returns `{check: {path: [message, ...]}}` or raises ToolNotRun. A tool that cannot run
NEVER passes quietly: the
engine prints NOT RUN with the reason, and a tool the project enabled at init FAILS the gate when it
later cannot run.

Tools are looked up in the project first (`.venv/bin`, `venv/bin`, `node_modules/.bin`) and then on
PATH. Nothing is downloaded: an auto-install hides which version judged the code.

To add a language: write `run_<tool>(root, budgets) -> dict`, register it in TOOLS with the file
suffixes that make it apply, and its findings flow into the same ratchet as every other check.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gate_code import JS_SUFFIXES

SECURITY_REVIEWED_RE = re.compile(r"#\s*security-reviewed:\s*\S.{2,}")
# Older ruff prints "Would reformat: path"; newer prints a diff headed " --> path:line:col".
RUFF_UNFORMATTED_RE = re.compile(r"^(?:Would reformat: (?P<old>.+)|\s*--> (?P<new>.+?):\d+:\d+)$")
COMPLEXITY_CODES = ("C901", "PLR0915", "PLR0913", "PLR0917")
MYPY_ERROR_RE = re.compile(r"^(?P<path>[^:\n]+\.pyi?):(?P<line>\d+)(?::\d+)?: error: (?P<msg>.*)$")
MYPY_FOUND_RE = re.compile(r"^Found (?P<n>\d+) errors? in \d+ files?")
TSC_ERROR_RE = re.compile(r"^(?P<path>.+?)\((?P<line>\d+),\d+\): error (?P<code>TS\d+): (?P<msg>.*)$")
ESLINT_COMPLEXITY_RULES = {"complexity", "max-statements", "max-params"}
TIMEOUT_SECONDS = 900


class ToolNotRunError(Exception):
    """A tool could not run, so its silence would be a lie."""


@dataclass(frozen=True)
class Budgets:
    """The "do less" budget every language enforces: branches, statements, parameters."""

    complexity: int = 10
    statements: int = 50
    args: int = 5


def find_tool(root: Path, name: str) -> str | None:
    """Return the project's own copy of a tool, else the one on PATH, else None."""
    for folder in (".venv/bin", "venv/bin", "node_modules/.bin"):
        candidate = root / folder / name
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def _run(root: Path, argv: list[str]) -> subprocess.CompletedProcess[str]:
    binary = find_tool(root, argv[0])
    if binary is None:
        raise ToolNotRunError(f"`{argv[0]}` is not installed in the project or on PATH")
    try:
        return subprocess.run(
            [binary, *argv[1:]], cwd=root, capture_output=True, text=True,
            timeout=TIMEOUT_SECONDS, stdin=subprocess.DEVNULL, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolNotRunError(f"`{argv[0]}` timed out after {TIMEOUT_SECONDS}s") from exc


def _rel(root: Path, raw: str) -> str:
    path = Path(raw)
    path = path if path.is_absolute() else root / path
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return raw


def _line_text(root: Path, rel: str, line_no: int) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace").splitlines()[line_no - 1]
    except (OSError, IndexError):
        return ""


# ---------------------------------------------------------------- Python
def run_ruff_format(root: Path, budgets: Budgets) -> dict[str, dict[str, list[str]]]:
    """Every file `ruff format` would rewrite is one format finding."""
    proc = _run(root, ["ruff", "format", "--check", "."])
    if proc.returncode not in (0, 1):
        raise ToolNotRunError(f"ruff format crashed: {proc.stderr.strip()[:300]}")
    files: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        match = RUFF_UNFORMATTED_RE.match(line)
        if match:
            files[_rel(root, (match["old"] or match["new"]).strip())] = ["not formatted"]
    if proc.returncode == 1 and not files:
        raise ToolNotRunError(f"ruff format reported changes this engine cannot read: {proc.stdout[:300]}")
    return {"format": files}


def run_ruff_lint(root: Path, budgets: Budgets) -> dict[str, dict[str, list[str]]]:
    """Ruff with the project's own rules plus the complexity budget and the security family (S)."""
    proc = _run(root, [
        "ruff", "check", ".", "--output-format", "json", "--no-fix",
        "--extend-select", "C901,PLR0915,PLR0913,S",
        "--config", f"lint.mccabe.max-complexity = {budgets.complexity}",
        "--config", f"lint.pylint.max-statements = {budgets.statements}",
        "--config", f"lint.pylint.max-args = {budgets.args}",
    ])
    if proc.returncode not in (0, 1):
        raise ToolNotRunError(f"ruff check failed (exit {proc.returncode}): {proc.stderr.strip()[:300]}")
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ToolNotRunError(f"ruff check gave no JSON: {proc.stderr.strip()[:300]}") from exc
    out: dict[str, dict[str, list[str]]] = {"lint": {}, "complexity": {}, "security": {}}
    for row in rows:
        rel = _rel(root, row["filename"])
        code = row.get("code") or "syntax"
        line_no = row["location"]["row"]
        if code.startswith(COMPLEXITY_CODES):
            check = "complexity"
        elif code.startswith("S") and code[1:].isdigit():
            if SECURITY_REVIEWED_RE.search(_line_text(root, rel, line_no)):
                continue  # reviewed and explained on the line ruff names
            check = "security"
        else:
            check = "lint"
        out[check].setdefault(rel, []).append(f"line {line_no} {code} {row['message']}")
    return out


def run_mypy(root: Path, budgets: Budgets) -> dict[str, dict[str, list[str]]]:
    """Type errors per file; the counts must add up to mypy's own closing line or the read fails."""
    proc = _run(root, ["mypy", "."])
    text = proc.stdout + proc.stderr
    if "errors prevented further checking" in text or "INTERNAL ERROR" in text or "Traceback" in text:
        raise ToolNotRunError(f"mypy stopped before checking: {text.strip()[-300:]}")
    files: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        match = MYPY_ERROR_RE.match(line)
        if match:
            files.setdefault(_rel(root, match["path"]), []).append(f"line {match['line']} {match['msg']}")
    total = sum(len(v) for v in files.values())
    found = next((MYPY_FOUND_RE.match(line) for line in proc.stdout.splitlines() if MYPY_FOUND_RE.match(line)), None)
    if found and int(found["n"]) != total:
        raise ToolNotRunError(f"read {total} mypy errors but mypy says {found['n']}; refusing a partial read")
    if not found and (total or proc.returncode not in (0,)):
        raise ToolNotRunError(f"mypy ended without its summary line: {text.strip()[-300:]}")
    return {"types": files}


# ---------------------------------------------------------------- Node / TypeScript
def run_prettier(root: Path, budgets: Budgets) -> dict[str, dict[str, list[str]]]:
    """Every source file prettier would rewrite is one format finding."""
    proc = _run(root, ["prettier", "--list-different", "."])
    if proc.returncode not in (0, 1):
        raise ToolNotRunError(f"prettier crashed: {proc.stderr.strip()[:300]}")
    files = {
        _rel(root, line.strip()): ["not formatted"]
        for line in proc.stdout.splitlines()
        if Path(line.strip()).suffix in JS_SUFFIXES
    }
    return {"format": files}


def run_eslint(root: Path, budgets: Budgets) -> dict[str, dict[str, list[str]]]:
    """ESLint with the project's config plus the complexity budget rules."""
    proc = _run(root, [
        "eslint", ".", "-f", "json",
        "--rule", f"complexity: [error, {budgets.complexity}]",
        "--rule", f"max-statements: [error, {budgets.statements}]",
        "--rule", f"max-params: [error, {budgets.args}]",
    ])
    if proc.returncode not in (0, 1):
        raise ToolNotRunError(f"eslint failed (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[:300]}")
    try:
        results = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ToolNotRunError(f"eslint crashed: {(proc.stderr or proc.stdout).strip()[:300]}") from exc
    out: dict[str, dict[str, list[str]]] = {"lint": {}, "complexity": {}}
    for result in results:
        rel = _rel(root, result["filePath"])
        for message in result.get("messages", []):
            rule = message.get("ruleId") or ("parse-error" if message.get("fatal") else "eslint-directive")
            check = "complexity" if rule in ESLINT_COMPLEXITY_RULES else "lint"
            out[check].setdefault(rel, []).append(f"line {message.get('line')} {rule} {message['message']}")
    return out


def run_tsc(root: Path, budgets: Budgets) -> dict[str, dict[str, list[str]]]:
    """Type errors per file from `tsc --noEmit`; a failure with no readable errors is a crash."""
    proc = _run(root, ["tsc", "--noEmit", "--pretty", "false", "-p", "."])
    files: dict[str, list[str]] = {}
    unread = []
    for line in proc.stdout.splitlines():
        match = TSC_ERROR_RE.match(line)
        if match:
            files.setdefault(_rel(root, match["path"]), []).append(f"line {match['line']} {match['code']} {match['msg']}")
        elif line.startswith("error TS"):
            unread.append(line)
    if unread or (proc.returncode != 0 and not files):
        raise ToolNotRunError(f"tsc failed without per-file errors: {(unread or [proc.stdout + proc.stderr])[0][:300]}")
    return {"types": files}


Runner = Callable[[Path, Budgets], dict[str, dict[str, list[str]]]]

# tool name -> (language, runner, a file that must exist for the tool to apply, or None)
TOOLS: dict[str, tuple[str, Runner, str | None]] = {
    "ruff-format": ("python", run_ruff_format, None),
    "ruff-lint": ("python", run_ruff_lint, None),
    "mypy": ("python", run_mypy, None),
    "prettier": ("node", run_prettier, None),
    "eslint": ("node", run_eslint, "eslint.config.*"),
    "tsc": ("node", run_tsc, "tsconfig.json"),
}
BINARIES = {"ruff-format": "ruff", "ruff-lint": "ruff"}


def detect(root: Path, languages: set[str]) -> tuple[list[str], list[str]]:
    """Return (tools to enable, plain reasons for every tool left off) for the given languages."""
    enabled, skipped = [], []
    for name, (language, _runner, needs) in TOOLS.items():
        if language not in languages:
            continue
        if needs and not any(root.glob(needs)):
            skipped.append(f"{name}: no {needs} in the project")
        elif find_tool(root, BINARIES.get(name, name)) is None:
            skipped.append(f"{name}: not installed in the project or on PATH")
        else:
            enabled.append(name)
    return enabled, skipped
