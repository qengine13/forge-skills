"""DOM/CSS structure gate — hidden HTML and CSS implementation debt, baseline-ratcheted.

The visual result can be unchanged while the implementation gets worse: deeper DOMs, wrapper
chains, utility soup in semantic templates, inline styles/scripts, brittle selector chains, and
hardcoded layout pixels all raise the future cost of a screen. This gate counts those debt
classes per file and compares them to a committed baseline:

* count > baseline -> FAIL: new or worsened structure debt is forbidden.
* count = baseline -> PASS.
* count < baseline -> PASS with a notice to lower the baseline.

The first committed baseline freezes today's debt. New files are born clean because an absent
baseline means zero allowed debt. Deliberate exceptions: put `slopgate-allow: <category> <reason>`
on the line or the line above.

Adapted from the owner's source structure gate; generalized to config-driven globs and budgets.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
GENERIC_WRAPPER_TAGS = {"div", "span"}
INTERACTIVE_ANCESTOR_TAGS = {"a", "button"}
INTERACTIVE_CHILD_TAGS = {"a", "button", "input", "select", "textarea"}

UTILITY_CLASS_RE = re.compile(
    r"^(?:-)?(?:"
    r"flex|grid|block|inline|inline-block|inline-flex|hidden|"
    r"(?:items|justify|content|self|place)-[a-z0-9_.:/\[\]()%!-]+|"
    r"(?:gap|gap-x|gap-y|p|px|py|pt|pr|pb|pl|m|mx|my|mt|mr|mb|ml)-[a-z0-9_.:/\[\]()%!-]+|"
    r"(?:w|h|min-w|max-w|min-h|max-h)-[a-z0-9_.:/\[\]()%!-]+|"
    r"(?:text|bg|border|rounded|shadow|font|leading|tracking|opacity|overflow|overflow-x|"
    r"overflow-y|grid-cols|col-span|row-span)-[a-z0-9_.:/\[\]()%!-]+|"
    r"z-(?:auto|\d+|\[[^\]]+\])"
    r")$"
)

ALLOW_MARKER = "slopgate-allow"
ALLOW_RE = re.compile(r"slopgate-allow\s*:\s*(?P<category>[a-z0-9-]+)\s+\S")
BARE_ALLOW_RE = re.compile(r"slopgate-allow(?!\s*:)")
COMMENT_BLOCK_RE = re.compile(
    r"<!--.*?-->|{#.*?#}|{%\s*comment(?:\s+[^%]*)?%}.*?{%\s*endcomment\s*%}",
    re.DOTALL | re.IGNORECASE,
)

STRUCTURAL_PX_DECL_RE = re.compile(
    r"(?:^|[;{])\s*"
    r"(?P<prop>"
    r"(?:min-|max-)?(?:width|height)|"
    r"(?:min-|max-)?(?:inline|block)-size|"
    r"grid-template-(?:columns|rows)|"
    r"flex-basis|"
    r"top|right|bottom|left|"
    r"inset(?:-(?:inline|block))?(?:-(?:start|end))?"
    r")\s*:\s*(?P<value>[^;{}]*)"
)
PX_RE = re.compile(r"(-?\d+(?:\.\d+)?)px\b")
CUSTOM_PROP_RE = re.compile(r"\s*--[\w-]+\s*:")
IMPORTANT_RE = re.compile(r"!important\b")
CLASS_OR_ID_RE = re.compile(r"[.#][A-Za-z0-9_-]+")

CATEGORIES: dict[str, str] = {
    "html-max-depth": "DOM depth over the screen budget; usually means layout wrappers are doing too much.",
    "html-wrapper-chain": "Three or more single-child div/span wrappers; same pixels, more brittle DOM.",
    "html-utility-class": "Utility-token class in a semantic template; use semantic classes (disable for Tailwind projects).",
    "html-inline-style": "Inline style attribute in production markup.",
    "html-style-block": "Inline style block in a template; styling belongs in the stylesheet layer.",
    "html-inline-script": "Inline script block in a template; behavior belongs in JS modules.",
    "html-nested-interactive": "Interactive element nested inside another interactive element.",
    "css-layout-px": "Hardcoded structural pixel dimensions in source CSS.",
    "css-selector-complexity": "Selector chains over the complexity budget.",
    "css-important": "Use of !important in source CSS.",
}

WEIGHTS: dict[str, int] = {
    "html-max-depth": 2,
    "html-wrapper-chain": 3,
    "html-utility-class": 3,
    "html-inline-style": 4,
    "html-style-block": 5,
    "html-inline-script": 5,
    "html-nested-interactive": 5,
    "css-layout-px": 2,
    "css-selector-complexity": 3,
    "css-important": 4,
}


@dataclass(frozen=True)
class DomBudgets:
    """The structure budgets one project runs under (config-overridable)."""

    depth: int = 10
    wrapper_chain: int = 3
    selector_parts: int = 4
    selector_classes: int = 5
    utility_classes_forbidden: bool = True


@dataclass(frozen=True)
class Debt:
    """One counted structure debt item; depth counts by excess, not by line."""

    file: str
    category: str
    line: int
    message: str
    count: int = 1


@dataclass(frozen=True)
class Finding:
    """One gate finding, either a blocking failure or a pass-with-notice shrink."""

    key: str
    severity: str
    message: str


@dataclass
class HtmlNode:
    """A parsed HTML element node used for structure checks."""

    tag: str
    attrs: dict[str, str | None]
    line: int
    children: list["HtmlNode"] = field(default_factory=list)
    text: str = ""


class TemplateParser(HTMLParser):
    """Best-effort HTML parser for server templates.

    Template tags are treated as text; malformed fragments are tolerated the same way browsers
    tolerate partial HTML.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.root = HtmlNode("root", {}, 1)
        self.stack: list[HtmlNode] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HtmlNode(
            tag.lower(), {name.lower(): value for name, value in attrs}, self.getpos()[0]
        )
        self.stack[-1].children.append(node)
        if node.tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HtmlNode(
            tag.lower(), {name.lower(): value for name, value in attrs}, self.getpos()[0]
        )
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        target = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == target:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.stack[-1].text += data


def scan_structure(
    root: Path,
    html_paths: list[Path],
    css_paths: list[Path],
    budgets: DomBudgets,
) -> list[Debt]:
    """Scan the given templates and stylesheets for structure debt."""
    debts: list[Debt] = []
    for path in html_paths:
        rel = path.relative_to(root).as_posix()
        debts.extend(
            _scan_html(rel, path.read_text(encoding="utf-8", errors="replace"), budgets)
        )
    for path in css_paths:
        rel = path.relative_to(root).as_posix()
        debts.extend(_scan_css(rel, path.read_text(encoding="utf-8", errors="replace"), budgets))
    return debts


def _scan_html(rel: str, text: str, budgets: DomBudgets) -> list[Debt]:
    """Scan one template for DOM and markup structure debt."""
    lines = text.splitlines()
    parser = TemplateParser()
    parser.feed(_mask_template_comments(text))
    debts: list[Debt] = []
    for node in _walk(parser.root):
        if node.tag == "root":
            continue
        if "style" in node.attrs and not _is_allowed(lines, node.line, "html-inline-style"):
            debts.append(Debt(rel, "html-inline-style", node.line, "inline style attribute"))
        if node.tag == "style" and not _is_allowed(lines, node.line, "html-style-block"):
            debts.append(Debt(rel, "html-style-block", node.line, "inline style block"))
        if (
            node.tag == "script"
            and "src" not in node.attrs
            and not _is_allowed(lines, node.line, "html-inline-script")
        ):
            debts.append(Debt(rel, "html-inline-script", node.line, "inline script block"))
        if budgets.utility_classes_forbidden:
            classes = node.attrs.get("class") or ""
            for token in classes.split():
                if _is_template_token(token) or not UTILITY_CLASS_RE.match(token):
                    continue
                if not _is_allowed(lines, node.line, "html-utility-class"):
                    debts.append(
                        Debt(
                            rel,
                            "html-utility-class",
                            node.line,
                            f"utility-looking class `{token}`",
                        )
                    )
    debts.extend(_html_depth_debt(rel, parser.root, lines, budgets))
    debts.extend(_wrapper_chain_debt(rel, parser.root, lines, budgets))
    debts.extend(_nested_interactive_debt(rel, parser.root, lines, []))
    return debts


def _mask_template_comments(text: str) -> str:
    """Remove HTML and template comments while preserving parser line numbers."""

    def keep_newlines(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    return COMMENT_BLOCK_RE.sub(keep_newlines, text)


def _scan_css(rel: str, text: str, budgets: DomBudgets) -> list[Debt]:
    """Scan one CSS/SCSS source file for structure debt.

    Debt is counted on COMMENT-MASKED lines (`/* legacy z-index: 9999 */` is not debt - a
    false-positive generator gets the gate muted), while the
    allow-marker lookup reads the RAW lines, because that is exactly where the marker lives.
    """
    lines = text.splitlines()
    masked = mask_css_comments(text).splitlines()
    debts: list[Debt] = []
    for index, line in enumerate(masked, start=1):
        if _is_allowed(lines, index, "css-important"):
            pass
        elif IMPORTANT_RE.search(line):
            debts.append(Debt(rel, "css-important", index, "!important declaration"))

        if not _is_allowed(lines, index, "css-layout-px"):
            debts.extend(_css_layout_px_debt(rel, index, line))
        if not _is_allowed(lines, index, "css-selector-complexity"):
            debts.extend(_css_selector_debt(rel, index, line, budgets))
    return debts


CSS_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# `//` starts an SCSS comment only after whitespace/line-start/`;`/`{` - never inside url(https://...).
SCSS_LINE_COMMENT_RE = re.compile(r"(?:(?<=^)|(?<=[\s;{]))//.*$", re.MULTILINE)


def mask_css_comments(text: str) -> str:
    """Blank comments to spaces, preserving newlines so line numbers stay true."""

    def keep_newlines(match: re.Match[str]) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))

    return SCSS_LINE_COMMENT_RE.sub(" ", CSS_BLOCK_COMMENT_RE.sub(keep_newlines, text))


def _walk(node: HtmlNode) -> list[HtmlNode]:
    nodes = [node]
    for child in node.children:
        nodes.extend(_walk(child))
    return nodes


def _is_template_token(token: str) -> bool:
    """Return whether a class token is template syntax rather than a class."""
    return any(marker in token for marker in ("{", "}", "%", "|", ":"))


def _html_depth_debt(
    rel: str, root: HtmlNode, lines: list[str], budgets: DomBudgets
) -> list[Debt]:
    """Return debt for maximum DOM depth above the configured budget."""
    max_depth = 0
    max_line = 1

    def visit(node: HtmlNode, depth: int) -> None:
        nonlocal max_depth, max_line
        if node.tag != "root" and depth > max_depth:
            max_depth = depth
            max_line = node.line
        for child in node.children:
            visit(child, depth + 1)

    visit(root, 0)
    excess = max(0, max_depth - budgets.depth)
    if excess == 0 or _is_allowed(lines, max_line, "html-max-depth"):
        return []
    return [
        Debt(
            rel,
            "html-max-depth",
            max_line,
            f"DOM depth {max_depth} exceeds budget {budgets.depth}",
            count=excess,
        )
    ]


def _wrapper_chain_debt(
    rel: str, root: HtmlNode, lines: list[str], budgets: DomBudgets
) -> list[Debt]:
    """Return debt for long single-child div/span wrapper chains."""
    debts: list[Debt] = []

    def visit(node: HtmlNode, parent_qualifies: bool) -> None:
        qualifies = _is_single_child_wrapper(node)
        if qualifies and not parent_qualifies:
            chain = [node]
            current = node
            while current.children and _is_single_child_wrapper(current.children[0]):
                current = current.children[0]
                chain.append(current)
            if len(chain) >= budgets.wrapper_chain and not _is_allowed(
                lines, chain[0].line, "html-wrapper-chain"
            ):
                debts.append(
                    Debt(
                        rel,
                        "html-wrapper-chain",
                        chain[0].line,
                        f"{len(chain)} single-child div/span wrappers in a row",
                        count=len(chain) - budgets.wrapper_chain + 1,
                    )
                )
        for child in node.children:
            visit(child, qualifies)

    visit(root, False)
    return debts


def _is_single_child_wrapper(node: HtmlNode) -> bool:
    """Return whether a node is a generic wrapper around exactly one element and no text."""
    return (
        node.tag in GENERIC_WRAPPER_TAGS
        and len(node.children) == 1
        and not _meaningful_text(node.text)
    )


def _meaningful_text(text: str) -> bool:
    """Return whether text is user-visible content rather than whitespace or template syntax."""
    stripped = text.strip()
    if not stripped:
        return False
    return not (stripped.startswith("{%") and stripped.endswith("%}"))


def _nested_interactive_debt(
    rel: str, node: HtmlNode, lines: list[str], interactive_stack: list[HtmlNode]
) -> list[Debt]:
    """Return debt for interactive controls nested inside other interactive controls."""
    debts: list[Debt] = []
    is_nested_interactive = node.tag in INTERACTIVE_CHILD_TAGS and interactive_stack
    if is_nested_interactive and not _is_allowed(lines, node.line, "html-nested-interactive"):
        debts.append(
            Debt(
                rel,
                "html-nested-interactive",
                node.line,
                f"`{node.tag}` nested inside `{interactive_stack[-1].tag}`",
            )
        )
    next_stack = (
        [*interactive_stack, node] if node.tag in INTERACTIVE_ANCESTOR_TAGS else interactive_stack
    )
    for child in node.children:
        debts.extend(_nested_interactive_debt(rel, child, lines, next_stack))
    return debts


def _css_layout_px_debt(rel: str, line_no: int, line: str) -> list[Debt]:
    """Return hardcoded structural-pixel debt on one CSS line."""
    if CUSTOM_PROP_RE.match(line):
        return []
    debts: list[Debt] = []
    for match in STRUCTURAL_PX_DECL_RE.finditer(line):
        prop = match.group("prop")
        for component in PX_RE.findall(match.group("value")):
            if float(component) == 0:
                continue
            debts.append(Debt(rel, "css-layout-px", line_no, f"{prop} uses {component}px"))
    return debts


def _css_selector_debt(rel: str, line_no: int, line: str, budgets: DomBudgets) -> list[Debt]:
    """Return selector-complexity debt on one CSS line."""
    if "{" not in line:
        return []
    selector_text = _strip_css_comment(line.split("{", 1)[0]).strip()
    if not selector_text or selector_text.startswith("@") or selector_text in {"from", "to"}:
        return []
    if selector_text.endswith("%"):
        return []
    debts: list[Debt] = []
    for selector in selector_text.split(","):
        selector = selector.strip()
        if not selector:
            continue
        parts = [part for part in re.split(r"\s+|>|~|\+", selector) if part]
        class_or_id_count = len(CLASS_OR_ID_RE.findall(selector))
        if len(parts) > budgets.selector_parts:
            debts.append(
                Debt(
                    rel,
                    "css-selector-complexity",
                    line_no,
                    f"selector has {len(parts)} parts: `{selector[:80]}`",
                )
            )
        elif class_or_id_count > budgets.selector_classes:
            debts.append(
                Debt(
                    rel,
                    "css-selector-complexity",
                    line_no,
                    f"selector has {class_or_id_count} class/id hooks: `{selector[:80]}`",
                )
            )
    return debts


def _strip_css_comment(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text)


def _is_allowed(lines: list[str], line_no: int, category: str) -> bool:
    """Return whether the current or previous line carries a category-specific allowance."""
    for index in (line_no, line_no - 1):
        if index < 1 or index > len(lines):
            continue
        match = ALLOW_RE.search(lines[index - 1])
        if match and match.group("category") == category:
            return True
    return False


def bare_allow_failures(root: Path, paths: list[Path]) -> list[Finding]:
    """Unconditional failures for malformed allowance markers.

    These are FINDINGS, never Debt: a Debt would enter the baseline at init and a cargo-culted
    bare marker would be grandfathered forever. A marker without
    `: <category> <reason>` fails every run until it is completed or removed.
    """
    failures: list[Finding] = []
    for path in paths:
        rel = path.relative_to(root).as_posix()
        for index, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            if ALLOW_MARKER not in line:
                continue
            if BARE_ALLOW_RE.search(line) or not ALLOW_RE.search(line):
                failures.append(
                    Finding(
                        f"{rel}:{index}",
                        "fail",
                        f"bare `{ALLOW_MARKER}` marker; use "
                        f"`{ALLOW_MARKER}: <category> <reason>` - never baselined",
                    )
                )
    return failures


def _counts_by_key(debts: list[Debt]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for debt in debts:
        key = f"{debt.file}::{debt.category}"
        counts[key] = counts.get(key, 0) + debt.count
    return counts


def _load_baselines(path: Path) -> tuple[dict[str, int], list[Finding]]:
    """Load a baseline file, failing closed on missing or corrupt data."""
    if not path.exists():
        return {}, [
            Finding("-", "fail", f"{path} not found; run `slopgate.py update` and commit it")
        ]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [Finding("-", "fail", f"{path} is not valid JSON ({exc}); failing closed")]
    if not isinstance(raw, dict):
        return {}, [Finding("-", "fail", f"{path} must contain a JSON object; failing closed")]
    baselines: dict[str, int] = {}
    for key, entry in raw.items():
        baseline = entry.get("baseline") if isinstance(entry, dict) else None
        # bool is an int subclass: `"baseline": true` must fail closed, not permit one debt item.
        if isinstance(baseline, bool) or not isinstance(baseline, int) or baseline < 0:
            return {}, [Finding(key, "fail", f"invalid baseline entry for {key}; failing closed")]
        category = key.rsplit("::", 1)[-1]
        if category not in CATEGORIES:
            return {}, [Finding(key, "fail", f"unknown dom-gate category `{category}`")]
        baselines[key] = entry["baseline"]
    return baselines, []


def check(
    root: Path,
    html_paths: list[Path],
    css_paths: list[Path],
    budgets: DomBudgets,
    baselines_path: Path,
) -> list[Finding]:
    """Compare current structure debt against committed baselines."""
    baselines, findings = _load_baselines(baselines_path)
    if findings:
        return findings
    counts = _counts_by_key(scan_structure(root, html_paths, css_paths, budgets))
    for key in sorted(set(counts) | set(baselines)):
        baseline = baselines.get(key, 0)
        current = counts.get(key, 0)
        if current > baseline:
            findings.append(
                Finding(
                    key,
                    "fail",
                    f"count grew {baseline} -> {current} (+{current - baseline}) - "
                    "new or worsened structure debt is forbidden",
                )
            )
        elif current < baseline:
            findings.append(
                Finding(
                    key,
                    "notice",
                    f"count shrank {baseline} -> {current} (-{baseline - current}) - "
                    "lock it in with `slopgate.py update`",
                )
            )
    return findings


def update_baselines(
    root: Path,
    html_paths: list[Path],
    css_paths: list[Path],
    budgets: DomBudgets,
    baselines_path: Path,
) -> list[Finding]:
    """Rewrite baselines downward to the current counts.

    A missing baseline file bootstraps the initial snapshot. An existing file never grows and
    never accepts a new debt key; new debt must be removed or explicitly allowed before updating.
    """
    counts = _counts_by_key(scan_structure(root, html_paths, css_paths, budgets))
    if baselines_path.exists():
        baselines, findings = _load_baselines(baselines_path)
        if findings:
            return findings
        next_payload: dict[str, dict[str, object]] = {}
        for key in sorted(set(counts) | set(baselines)):
            current = counts.get(key, 0)
            baseline = baselines.get(key)
            if baseline is None and current > 0:
                findings.append(
                    Finding(
                        key,
                        "fail",
                        "new debt key is absent from the committed baseline; fix it instead of "
                        "grandfathering it",
                    )
                )
                continue
            if baseline is not None and current > baseline:
                findings.append(
                    Finding(
                        key,
                        "fail",
                        f"refusing to raise baseline {baseline} -> {current}; pay the debt down",
                    )
                )
                next_payload[key] = _baseline_entry(key, baseline)
                continue
            if current > 0:
                next_payload[key] = _baseline_entry(key, current)
                if baseline is None or current < baseline:
                    findings.append(Finding(key, "notice", f"baseline lowered to {current}"))
        baselines_path.write_text(_json_dumps(next_payload), encoding="utf-8")
        return findings

    payload = {
        key: _baseline_entry(key, count) for key, count in sorted(counts.items()) if count > 0
    }
    baselines_path.write_text(_json_dumps(payload), encoding="utf-8")
    return [
        Finding(key, "notice", f"baseline recorded at {entry['baseline']}")
        for key, entry in payload.items()
    ]


def _baseline_entry(key: str, count: int) -> dict[str, object]:
    category = key.rsplit("::", 1)[-1]
    return {"baseline": count, "description": CATEGORIES[category]}


def _json_dumps(payload: dict[str, dict[str, object]]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def render_report(
    root: Path,
    html_paths: list[Path],
    css_paths: list[Path],
    budgets: DomBudgets,
    limit: int = 25,
) -> str:
    """Render a ranked audit report of current structure debt."""
    debts = scan_structure(root, html_paths, css_paths, budgets)
    counts = _counts_by_key(debts)
    score_by_file: dict[str, int] = {}
    for key, count in counts.items():
        file, category = key.rsplit("::", 1)
        score_by_file[file] = score_by_file.get(file, 0) + count * WEIGHTS[category]
    lines = ["DOM/CSS structure debt report", ""]
    for rank, (file, score) in enumerate(
        sorted(score_by_file.items(), key=lambda item: (-item[1], item[0]))[:limit],
        start=1,
    ):
        lines.append(f"{rank}. {file} - score {score}")
        for key, count in sorted((k, v) for k, v in counts.items() if k.startswith(f"{file}::")):
            category = key.rsplit("::", 1)[-1]
            lines.append(f"   - {category}: {count}")
    if len(score_by_file) > limit:
        lines.append(f"... {len(score_by_file) - limit} more files omitted")
    if not score_by_file:
        lines.append("clean - no structure debt found")
    return "\n".join(lines)
