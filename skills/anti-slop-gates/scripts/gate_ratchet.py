"""Ratchet gate for grandfathered debt — the counters only turn DOWN.

Some debt classes are grandfathered: blocking them outright would red every commit (the tree
carries pre-existing copies), but every NEW copy is a regression. This gate recounts each class
with a cheap deterministic text scan and compares against the committed baselines:

* count > baseline -> FAIL - new copies of retired patterns are forbidden; use the remedy.
* count = baseline -> PASS.
* count < baseline -> PASS with a notice to lower the baseline. `update` rewrites the file to
  the current counts - only ever DOWNWARD; it refuses to raise a baseline (growth is paid down,
  never re-baselined).

Ratchets are defined in the project's .slopgates/config.toml `[[ratchet]]` tables; `init` writes
a default set for web trees.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Ratchet:
    """One grandfathered-debt class whose tree-wide count may only shrink."""

    name: str
    description: str
    remedy: str  # what a new copy should have used instead
    suffixes: tuple[str, ...]  # file suffixes in scope (".html", ".css", ...)
    pattern: re.Pattern[str]  # one count per match on an in-scope line
    exclude_line: re.Pattern[str] | None = None  # a line matching this is the remedy itself


@dataclass(frozen=True)
class Finding:
    """One ratchet verdict - a failure that reds the gate or a pass-with-notice."""

    ratchet: str
    severity: str
    message: str


DEFAULT_RATCHETS: tuple[dict[str, object], ...] = (
    {
        "name": "inline-event-handler",
        "description": "onclick=/onchange=/... attributes in markup; behavior belongs in JS modules.",
        "remedy": "addEventListener from a JS module (or the framework's event binding)",
        "suffixes": [".html", ".htm"],
        "pattern": r"\bon(?:click|change|submit|input|load|mouseover|mouseout|keyup|keydown|focus|blur)\s*=",
    },
    {
        "name": "nbsp-spacing",
        "description": "Runs of &nbsp; used as layout; spacing belongs to CSS, not the text layer.",
        "remedy": "margin/padding/gap in the stylesheet",
        "suffixes": [".html", ".htm"],
        "pattern": r"(?:&nbsp;){2,}",
    },
    {
        "name": "br-stacking",
        "description": "<br><br> runs used as vertical rhythm; spacing belongs to CSS.",
        "remedy": "margin/padding/gap in the stylesheet",
        "suffixes": [".html", ".htm"],
        "pattern": r"<br\s*/?>\s*<br\s*/?>",
    },
    {
        "name": "z-index-extreme",
        "description": "z-index of 999+ - an arms race, not a layering system.",
        "remedy": "a small documented z-scale (tokens), not four-digit escalation",
        "suffixes": [".css", ".scss", ".html", ".htm"],
        "pattern": r"z-index\s*:\s*9{3,}",
    },
)


def ratchets_from_config(entries: list[dict[str, object]]) -> tuple[list[Ratchet], list[Finding]]:
    """Build the ratchet registry from config `[[ratchet]]` tables, failing closed on bad rows."""
    ratchets: list[Ratchet] = []
    findings: list[Finding] = []
    for entry in entries:
        name = str(entry.get("name", ""))
        raw_pattern = entry.get("pattern")
        if not name or not isinstance(raw_pattern, str):
            findings.append(
                Finding(name or "-", "fail", "ratchet entry needs `name` and `pattern` strings")
            )
            continue
        try:
            pattern = re.compile(raw_pattern)
        except re.error as exc:
            findings.append(Finding(name, "fail", f"invalid ratchet pattern ({exc})"))
            continue
        exclude = entry.get("exclude_line")
        ratchets.append(
            Ratchet(
                name=name,
                description=str(entry.get("description", "")),
                remedy=str(entry.get("remedy", "the project's minted primitive")),
                suffixes=tuple(str(s) for s in entry.get("suffixes", [".html"])),
                pattern=pattern,
                exclude_line=re.compile(str(exclude)) if isinstance(exclude, str) else None,
            )
        )
    return ratchets, findings


CSS_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
SCSS_LINE_COMMENT_RE = re.compile(r"(?:(?<=^)|(?<=[\s;{]))//.*$", re.MULTILINE)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _mask_comments(path: Path, text: str) -> str:
    """Blank comments before counting - `/* legacy z-index: 9999 */` is not debt.

    (Comment matches are a false-positive generator that gets the gate muted.) Newlines are
    preserved so per-line counting is unchanged.
    """

    def keep_newlines(match: re.Match[str]) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))

    if path.suffix in {".css", ".scss"}:
        return SCSS_LINE_COMMENT_RE.sub(" ", CSS_BLOCK_COMMENT_RE.sub(keep_newlines, text))
    if path.suffix in {".html", ".htm"}:
        return HTML_COMMENT_RE.sub(keep_newlines, text)
    return text


def count_ratchet(ratchet: Ratchet, root: Path, files: list[Path]) -> int:
    """Recount one ratchet's debt class across the in-scope files."""
    total = 0
    for path in _in_scope(ratchet, files):
        text = _mask_comments(path, path.read_text(encoding="utf-8", errors="replace"))
        for line in text.splitlines():
            if ratchet.exclude_line is not None and ratchet.exclude_line.search(line):
                continue
            total += len(ratchet.pattern.findall(line))
    return total


def _in_scope(ratchet: Ratchet, files: list[Path]) -> Iterator[Path]:
    for path in files:
        if path.suffix in ratchet.suffixes:
            yield path


def check(
    root: Path, files: list[Path], ratchets: list[Ratchet], baselines_path: Path
) -> list[Finding]:
    """Recount every ratchet and compare against the committed baselines."""
    baselines, findings = _load_baselines(baselines_path)
    if findings:
        return findings

    for ratchet in ratchets:
        baseline = baselines.get(ratchet.name)
        if baseline is None:
            findings.append(
                Finding(
                    ratchet.name,
                    "fail",
                    f"no baseline recorded in {baselines_path.name} - "
                    "run `slopgate.py update` and commit the file",
                )
            )
            continue
        current = count_ratchet(ratchet, root, files)
        if current > baseline:
            findings.append(
                Finding(
                    ratchet.name,
                    "fail",
                    f"count grew {baseline} -> {current} (+{current - baseline}) - new copies of "
                    f"retired patterns are forbidden - use the remedy ({ratchet.remedy})",
                )
            )
        elif current < baseline:
            findings.append(
                Finding(
                    ratchet.name,
                    "notice",
                    f"count shrank {baseline} -> {current} (-{baseline - current}) - lock it in: "
                    "`slopgate.py update` lowers the baseline",
                )
            )

    for stale in sorted(set(baselines) - {ratchet.name for ratchet in ratchets}):
        findings.append(
            Finding(
                stale,
                "fail",
                "stale baseline entry - no such ratchet in the config; "
                "run `slopgate.py update` to regenerate the file",
            )
        )
    return findings


def update_baselines(
    root: Path, files: list[Path], ratchets: list[Ratchet], baselines_path: Path
) -> list[Finding]:
    """Rewrite the baselines file to the current counts - only ever downward."""
    if baselines_path.exists():
        baselines, findings = _load_baselines(baselines_path)
        if findings:
            return findings  # never regenerate over a corrupt committed file silently
    else:
        baselines = {}

    findings: list[Finding] = []
    new_baselines: dict[str, int] = {}
    for ratchet in ratchets:
        current = count_ratchet(ratchet, root, files)
        baseline = baselines.get(ratchet.name)
        if baseline is None:
            new_baselines[ratchet.name] = current
            findings.append(
                Finding(ratchet.name, "notice", f"baseline recorded at current count {current}")
            )
        elif current > baseline:
            new_baselines[ratchet.name] = baseline
            findings.append(
                Finding(
                    ratchet.name,
                    "fail",
                    f"REFUSED to raise the baseline {baseline} -> {current} - the ratchet only "
                    f"turns down; pay the debt down instead ({ratchet.remedy})",
                )
            )
        else:
            new_baselines[ratchet.name] = current
            if current < baseline:
                findings.append(
                    Finding(ratchet.name, "notice", f"baseline lowered {baseline} -> {current}")
                )
    _write_baselines(baselines_path, ratchets, new_baselines)
    return findings


def _load_baselines(path: Path) -> tuple[dict[str, int], list[Finding]]:
    """Return (baseline-by-name, findings) - findings are set when the file is unusable."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, [
            Finding(
                "-",
                "fail",
                f"baselines file not found: {path} - run `slopgate.py update` and commit it",
            )
        ]
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [Finding("-", "fail", f"could not read baselines ({exc}); failing closed")]

    baselines: dict[str, int] = {}
    findings: list[Finding] = []
    if not isinstance(payload, dict):
        return {}, [Finding("-", "fail", "baselines file is not a JSON object; failing closed")]
    for name, entry in payload.items():
        baseline = entry.get("baseline") if isinstance(entry, dict) else None
        if isinstance(baseline, bool) or not isinstance(baseline, int) or baseline < 0:
            findings.append(
                Finding(name, "fail", f"baseline is not a non-negative integer (got {baseline!r})")
            )
            continue
        baselines[name] = baseline
    return baselines, findings


def _write_baselines(path: Path, ratchets: list[Ratchet], baselines: dict[str, int]) -> None:
    """Render the baselines file from the registry (self-documenting, registry-ordered)."""
    payload = {
        ratchet.name: {
            "description": ratchet.description,
            "baseline": baselines[ratchet.name],
        }
        for ratchet in ratchets
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
