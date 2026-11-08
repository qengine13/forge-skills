"""Language-neutral code checks and the per-file ratchet store they share.

Every check produces a count per file. The committed `.slopgates/code_baselines.json` holds, per
check, each file's ceiling. A file absent from the baselines is held at ZERO, so new files are born
clean; `update` only ever lowers a ceiling and refuses to raise one (per file, never a total,
because a total lets one fix pay for a new problem somewhere else). A moved or renamed file keeps its debt
only if its baseline entry moves with it; otherwise it arrives as a new file at zero.

Checks owned here (no external tools needed):
  silenced   a warning silenced with no written reason (noqa, type: ignore, eslint-disable,
             @ts-ignore, ...).
  docstring  a public Python def/class without a Google docstring carrying a purpose line AND an
             example; an exported TS/JS function/class without a TSDoc block carrying @example.
  duplicate  a block of N+ meaningful lines that appears elsewhere in the tree.
  prior-art  a REUSE/PORT/EXTEND verdict that cites no live path:line; a BUILD verdict with no probe.
  layout     a grab-bag file name (utils.py, helpers.ts, ...) or a source file outside source_roots.
  helper     a new copy of a concern the shared-helper register says already has a canonical home
             (a declared regex, generalised for any project).
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

PY_SUFFIXES = {".py"}
JS_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"}

# A silencer is a comment directive telling a checker to look away. It is accepted only with a
# written reason: ` -- <reason>` (ESLint's own syntax) or `reason: <text>` anywhere after it; the
# TypeScript directives also accept free text after them (their native form).
SILENCER_RE = re.compile(
    r"(?:#|//|/\*)\s*(?P<directive>noqa\b|ruff:\s*noqa\b|mypy:\s*ignore-errors\b|type:\s*ignore\b|pylint:\s*disable\b|nosec\b"
    r"|pyright:\s*ignore\b|eslint-disable(?:-next-line|-line)?\b|@ts-ignore\b"
    r"|@ts-expect-error\b|@ts-nocheck\b|istanbul\s+ignore\b|biome-ignore\b)(?P<rest>[^\n]*)"
)
REASON_RE = re.compile(r"(?:\s--\s+|reason:\s*)(?P<reason>\w[^*\n]{2,})")
TS_DIRECTIVES = ("@ts-ignore", "@ts-expect-error", "@ts-nocheck")

DISPOSITION_RE = re.compile(
    r"\b(?i:verdict|disposition)\s*:\s*\**(?P<disposition>BUILD|REUSE|PORT|EXTEND)\**"
)
CITE_RE = re.compile(
    r"(?P<path>[\w./-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|md|html|css|scss|json|toml|yml|yaml|sql|go|rs|rb))"
    r":(?P<line>\d+)"
)
PROBED_RE = re.compile(r"probed\s+\d{4}-\d{2}-\d{2}", re.IGNORECASE)
BACKTICK_COMMAND_RE = re.compile(r"`(?:grep|rg|git|find|ls|psql|gh|python3?|uv|npx|slopgate)\b[^`]*`")
EVIDENCE_WINDOW = 3

EXAMPLE_PY_RE = re.compile(r"^\s*(?:Examples?:|>>>)", re.MULTILINE)
TS_EXPORT_RE = re.compile(
    r"^export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|abstract\s+class)\s+(?P<name>\w+)"
    r"|^export\s+const\s+(?P<const>\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?"
    r"(?:function\b|(?:<[^>]*>\s*)?\((?:[^()]|\([^()]*\))*\)\s*(?::\s*[^=]+?)?\s*=>|\w+\s*=>)",
    re.MULTILINE,
)
DUPLICATE_SKIP_RE = re.compile(r"^(?:import |from \S+ import |export \* from|[)}\]];?,?$|else:?$)")


@dataclass(frozen=True)
class Finding:
    """One code-check verdict line: a failure that reds the gate, a notice, or a NOT RUN."""

    check: str
    severity: str  # "fail" | "notice" | "notrun"
    message: str


def is_test_path(rel: str) -> bool:
    """Return whether a repo-relative path is test code (tests/ dirs, test_ files, .test/.spec)."""
    parts = rel.split("/")
    name = parts[-1]
    return (
        any(part in {"tests", "test", "__tests__"} for part in parts[:-1])
        or name.startswith("test_")
        or name == "conftest.py"
        or ".test." in name
        or ".spec." in name
    )


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- silenced warnings
def _python_comment_lines(text: str) -> list[tuple[int, str]] | None:
    """Return (line, comment text) for real Python comments, or None when the file will not tokenize."""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        return [(tok.start[0], tok.string) for tok in tokens if tok.type == tokenize.COMMENT]
    except (tokenize.TokenError, SyntaxError):
        return None


def count_silenced(text: str, python: bool = False) -> list[int]:
    """Return the 1-based lines carrying a silencer with no written reason.

    Python files are read through the tokenizer, so `"# noqa"` inside a string is not a silencer;
    other languages are read line by line (a directive quoted in a JS string can false-positive).
    """
    comments = _python_comment_lines(text) if python else None
    rows = comments if comments is not None else list(enumerate(text.splitlines(), start=1))
    lines: list[int] = []
    for number, line in rows:
        for match in SILENCER_RE.finditer(line):
            rest = match.group("rest")
            if REASON_RE.search(rest):
                continue
            directive = match.group("directive")
            if directive.startswith(TS_DIRECTIVES) and len(rest.strip(" :*/").split()) >= 2:
                continue
            lines.append(number)
    return lines


# ---------------------------------------------------------------- docstrings
def _py_docstring_problem(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, example_needed: bool
) -> str | None:
    doc = ast.get_docstring(node)
    if not doc or not doc.strip().splitlines()[0].strip():
        return "no docstring with a purpose line"
    if example_needed and not EXAMPLE_PY_RE.search(doc):
        return "docstring has no Example(s): section or >>> usage"
    return None


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        names.add(target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", ""))
    return names


def python_docstring_gaps(text: str) -> list[str]:
    """Return one message per public def/class lacking a purpose line or an example."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []  # the linter/type checker reports syntax errors; not this check's job
    gaps: list[str] = []
    defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def visit(body: list[ast.stmt], owner: str) -> None:
        for node in body:
            if not isinstance(node, defs) or node.name.startswith("_"):
                continue
            decorators = set() if isinstance(node, ast.ClassDef) else _decorator_names(node)
            if "overload" in decorators:
                continue
            example_needed = not decorators & {"property", "setter", "cached_property"}
            problem = _py_docstring_problem(node, example_needed)
            if problem:
                gaps.append(f"line {node.lineno} {owner}{node.name}: {problem}")
            if isinstance(node, ast.ClassDef):
                visit(node.body, f"{owner}{node.name}.")

    visit(tree.body, "")
    return gaps


def _jsdoc_above(lines: list[str], index: int) -> str | None:
    """Return the /** ... */ block ending just above line `index` (decorators skipped), or None."""
    cursor = index - 1
    while cursor >= 0 and (not lines[cursor].strip() or lines[cursor].strip().startswith("@")):
        cursor -= 1
    if cursor < 0 or not lines[cursor].strip().endswith("*/"):
        return None
    end = cursor
    while cursor >= 0 and "/**" not in lines[cursor]:
        cursor -= 1
    return "\n".join(lines[cursor : end + 1]) if cursor >= 0 else None


def ts_docstring_gaps(text: str) -> list[str]:
    """Return one message per exported function/class lacking a TSDoc purpose line or @example."""
    lines = text.splitlines()
    gaps: list[str] = []
    for match in TS_EXPORT_RE.finditer(text):
        index = text.count("\n", 0, match.start())
        name = match.group("name") or match.group("const")
        block = _jsdoc_above(lines, index)
        if block is None:
            gaps.append(f"line {index + 1} {name}: no /** TSDoc */ block")
            continue
        prose = [
            line.strip(" /*") for line in block.splitlines() if line.strip(" /*")
        ]
        if not prose or prose[0].startswith("@"):
            gaps.append(f"line {index + 1} {name}: TSDoc has no purpose line")
        elif "@example" not in block:
            gaps.append(f"line {index + 1} {name}: TSDoc has no @example")
    return gaps


# ---------------------------------------------------------------- duplicates
def _normalised(text: str) -> list[tuple[int, str]]:
    kept: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = " ".join(raw.split())
        if len(line) < 4 or line.startswith(("#", "//", "*", "/*")) or DUPLICATE_SKIP_RE.match(line):
            continue
        kept.append((number, line))
    return kept


def duplicate_blocks(files: dict[str, str], min_lines: int) -> dict[str, list[str]]:
    """Return, per file, one message per duplicated block of `min_lines`+ meaningful lines.

    Windows of `min_lines` normalised lines are hashed across every file; consecutive duplicated
    windows merge into one block, so a 12-line copy is one finding, not seven.
    """
    seen: dict[str, list[tuple[str, int]]] = {}
    windows: dict[str, list[tuple[int, str]]] = {}
    for rel, text in files.items():
        kept = _normalised(text)
        rows = []
        for start in range(len(kept) - min_lines + 1):
            chunk = "\n".join(line for _, line in kept[start : start + min_lines])
            digest = hashlib.sha1(chunk.encode()).hexdigest()
            rows.append((kept[start][0], digest))
            seen.setdefault(digest, []).append((rel, kept[start][0]))
        windows[rel] = rows
    result: dict[str, list[str]] = {}
    for rel, rows in windows.items():
        previous_dup = False
        for line_no, digest in rows:
            others = [site for site in seen[digest] if site != (rel, line_no)]
            if others and not previous_dup:
                other_rel, other_line = others[0]
                result.setdefault(rel, []).append(
                    f"line {line_no}: block duplicated at {other_rel}:{other_line}"
                )
            previous_dup = bool(others)
    return result


# ---------------------------------------------------------------- prior-art receipts
def _cite_resolves(root: Path, raw: str, line_no: int, by_name: dict[str, list[Path]]) -> bool:
    direct = (root / raw).resolve()
    candidates = [direct] if direct.is_file() else by_name.get(Path(raw).name, [])
    for target in candidates:
        try:
            target.relative_to(root)
        except ValueError:
            continue
        if not target.as_posix().endswith(Path(raw).as_posix().lstrip("./")):
            continue
        if 0 < line_no <= len(_read(target).splitlines()):
            return True
    return False


def _prose_lines(text: str) -> list[str]:
    """Blank fenced code and blockquotes: a quoted verdict is someone else's sentence."""
    out: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            out.append("")
            continue
        out.append("" if fenced or line.lstrip().startswith(">") else line)
    return out


def prior_art_gaps(root: Path, text: str, by_name: dict[str, list[Path]]) -> list[str]:
    """Return one message per disposition whose receipt is missing or no longer resolves."""
    lines = _prose_lines(text)
    gaps: list[str] = []
    for index, line in enumerate(lines):
        match = DISPOSITION_RE.search(line)
        if not match:
            continue
        window = "\n".join(lines[max(0, index - EVIDENCE_WINDOW) : index + EVIDENCE_WINDOW + 1])
        disposition = match.group("disposition")
        if disposition == "BUILD":
            if not (PROBED_RE.search(window) or BACKTICK_COMMAND_RE.search(window)):
                gaps.append(
                    f"line {index + 1}: BUILD claims nothing exists but carries no probe "
                    "(`probed YYYY-MM-DD:` or the search command in backticks)"
                )
            continue
        cites = list(CITE_RE.finditer(window))
        if not cites:
            gaps.append(f"line {index + 1}: {disposition} names no prior art as path:line")
        elif not any(
            _cite_resolves(root, c.group("path"), int(c.group("line")), by_name) for c in cites
        ):
            shown = ", ".join(c.group(0) for c in cites)
            gaps.append(f"line {index + 1}: {disposition} cites {shown} but that line is gone")
    return gaps


# ---------------------------------------------------------------- layout + helpers
def layout_gaps(rel: str, forbidden: set[str], source_roots: list[str]) -> list[str]:
    """Return messages for a grab-bag file name or a source file outside the declared roots."""
    gaps = []
    if Path(rel).name in forbidden:
        gaps.append(
            f"`{Path(rel).name}` is a grab-bag name; give the concern its own well-named file"
        )
    if source_roots and not is_test_path(rel) and not any(
        rel == root or rel.startswith(root.rstrip("/") + "/") for root in source_roots
    ):
        gaps.append(f"source file outside the layout map's roots {source_roots}")
    return gaps


def helper_gaps(rel: str, text: str, helpers: list[dict]) -> list[str]:
    """Return one message per hand-rolled copy of a concern that has a canonical helper."""
    gaps = []
    for helper in helpers:
        if rel == helper["canonical"]:
            continue
        for match in re.finditer(helper["pattern"], text, re.MULTILINE):
            line_no = text.count("\n", 0, match.start()) + 1
            gaps.append(
                f"line {line_no}: hand-rolled `{helper['name']}` — use {helper['canonical']}"
                f" ({helper.get('remedy', 'the canonical helper')})"
            )
    return gaps


# ---------------------------------------------------------------- the ratchet store
def load_baselines(path: Path) -> tuple[dict[str, dict[str, int]], list[Finding]]:
    """Return the committed ceilings, failing closed on a corrupt or ill-typed file."""
    if not path.is_file():
        return {}, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [Finding("baselines", "fail", f"{path.name} is not valid JSON ({exc})")]
    if not isinstance(raw, dict):
        return {}, [Finding("baselines", "fail", f"{path.name}: top level must be an object")]
    bad = [
        f"{check}/{rel}"
        for check, files in raw.items()
        if isinstance(files, dict)
        for rel, count in files.items()
        if isinstance(count, bool) or not isinstance(count, int) or count < 0
    ]
    if bad or not all(isinstance(files, dict) for files in raw.values()):
        return {}, [Finding("baselines", "fail", f"{path.name}: non-count entries {bad[:5]}")]
    return raw, []


def compare(
    counts: dict[str, dict[str, list[str]]], baselines: dict[str, dict[str, int]]
) -> list[Finding]:
    """Judge each check file by file: growth in any one file fails on its own."""
    findings: list[Finding] = []
    for check, files in sorted(counts.items()):
        ceilings = baselines.get(check, {})
        for rel, messages in sorted(files.items()):
            ceiling = ceilings.get(rel, 0)
            if len(messages) > ceiling:
                detail = "; ".join(messages[:3]) + (" ..." if len(messages) > 3 else "")
                findings.append(
                    Finding(check, "fail", f"{rel}: {len(messages)} (limit {ceiling}) — {detail}")
                )
            elif len(messages) < ceiling:
                findings.append(
                    Finding(check, "notice", f"{rel}: {len(messages)} below limit {ceiling} — run `update` to lock it in")
                )
        for rel, ceiling in sorted(ceilings.items()):
            if rel not in files and ceiling:
                findings.append(
                    Finding(check, "notice", f"{rel}: 0 below limit {ceiling} — run `update` to lock it in")
                )
    return findings


def ratchet_down(
    counts: dict[str, dict[str, list[str]]],
    baselines: dict[str, dict[str, int]],
    path: Path,
    bootstrap: bool,
) -> list[Finding]:
    """Lower every scanned ceiling to today's count; refuse any raise (unless bootstrapping).

    Checks that did not run this time (a tool NOT RUN) keep their ceilings untouched — an update
    must never delete debt it could not see.
    """
    merged = {check: dict(files) for check, files in baselines.items()}
    refused: list[Finding] = []
    for check, files in counts.items():
        ceilings = merged.setdefault(check, {})
        for rel in set(files) | set(ceilings):
            count = len(files.get(rel, []))
            if not bootstrap and count > ceilings.get(rel, 0):
                refused.append(
                    Finding(check, "fail", f"{rel}: refusing to raise limit {ceilings.get(rel, 0)} to {count}")
                )
                continue
            if count:
                ceilings[rel] = count
            else:
                ceilings.pop(rel, None)
    if refused:
        return refused
    cleaned = {check: dict(sorted(files.items())) for check, files in sorted(merged.items()) if files}
    path.write_text(json.dumps(cleaned, indent=2) + "\n", encoding="utf-8")
    return []
