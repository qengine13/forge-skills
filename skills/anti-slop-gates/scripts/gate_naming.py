"""Naming gate — a surface must wear the project's OWN vocabulary, not an invented parallel one.
# Modified for Forge Skills: portable public review guidance; see collection NOTICE.

Detect class names outside a project's declared vocabulary. A fictional example:
`.tile-cell` duplicates the concept already named `.cell`. Tests of application
behaviour alone do not establish consistency with the declared vocabulary.

TWO CHECKS

  RENAMED   a template emits a class that is neither in the project vocabulary nor a declared
            addition. When the invented name CONTAINS a known one (`tile-cell` -> `cell`) the
            gate NAMES the class you should have used. That naming is the whole value: it turns
            "computer says no" into "this project already calls this `.cell` - use it".

  AI-TIC    the name itself reads machine-generated rather than human: stacked filler nouns
            (`content-wrapper-inner`), numbered generics (`div2`, `box3`), versioned/disposable
            suffixes (`-v2`, `-new`, `-final`), puffery prefixes (`enhanced-`, `awesome-`), or
            five-plus hyphen segments. Grandfathered names are reported as notes; a NEW name
            with a tic fails.

WHY THERE IS NO regenerate/--update

A frozen vocabulary is not a ratchet. A mode that re-derived the manifest from the templates
would let the next agent clear a failure by re-freezing their own drift - a rubber stamp wearing
a gate's clothes. Adding a class costs one manifest line with a reason. That cost IS the
mechanism. (`init` harvests once, to grandfather a brownfield tree; it refuses to run twice.)

Adapted from the owner's source collection; see NOTICE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]

from textstrip import rendered_text

# Literal classes in server templates AND className in JSX/TSX. Whitespace around `=` is legal
# HTML; `className={"a b"}` is the JSX literal form. Template
# control flow has been blanked to spaces by rendered_text before this runs.
CLASS_ATTR_RE = re.compile(r'class(?:Name)?\s*=\s*\{?\s*(?:"([^"]*)"|\'([^\']*)\')')

# Behavioural hooks, not visual parts - the design has no opinion on them and never will.
DATA_HOOK_PREFIXES = ("js-", "data-")

# Utility tokens are the DOM gate's business (and legitimate on Tailwind projects); the naming
# gate must not double-report them. Anything with a modifier colon or bracket is a utility.
UTILITY_TOKEN_RE = re.compile(r"[:\[\]/!]")

FILLER_NOUNS = {
    "wrapper", "container", "inner", "outer", "content", "holder", "box", "area", "section",
}
NUMBERED_GENERIC_RE = re.compile(
    r"^(?:div|span|box|item|element|section|block|col|row|text|btn|button)\d+$"
)
DISPOSABLE_SUFFIX_RE = re.compile(r"-(?:v\d+|new|old|temp|tmp|final|copy|test|fixed|updated)$")
PUFFERY_PREFIX_RE = re.compile(
    r"^(?:enhanced|improved|awesome|beautiful|sleek|stunning|fancy|modern|nice|perfect|super)-"
)
MAX_SEGMENTS = 4


@dataclass(frozen=True)
class NamingVerdict:
    """The gate's output: blocking failures and pass-through notes."""

    failures: list[str]
    notes: list[str]


def harvest(root: Path, html_paths: list[Path]) -> set[str]:
    """Return every literal class the tree currently emits (the init grandfather set)."""
    found: set[str] = set()
    for path in html_paths:
        found |= classes_in(path)
    return found


def classes_in(template: Path) -> set[str]:
    """Return every literal class a template actually renders.

    Comments and template tags are stripped first: a comment naming `.tile-cell` as the thing
    NOT to do must never be read as an emission of it.
    """
    body = rendered_text(template.read_text(encoding="utf-8", errors="replace"))
    return {
        name
        for groups in CLASS_ATTR_RE.findall(body)
        for name in (groups[0] or groups[1]).split()
        if name
        and not name.startswith(DATA_HOOK_PREFIXES)
        and not _looks_like_expression(name)
        and not UTILITY_TOKEN_RE.search(name)
    }


def _looks_like_expression(token: str) -> bool:
    """Return whether a token is code (JSX expression residue), not a class name."""
    return any(marker in token for marker in ("{", "}", "$", "(", ")", "`"))


def ai_tics(name: str) -> list[str]:
    """Return the machine-name tells a class name carries, empty when it reads human."""
    tics: list[str] = []
    segments = name.split("-")
    filler = [seg for seg in segments if seg in FILLER_NOUNS]
    if len(filler) >= 2:
        tics.append(f"stacked filler nouns ({'+'.join(filler)}) - name the PART, not the wrapping")
    if NUMBERED_GENERIC_RE.match(name):
        tics.append("numbered generic - a person names the thing, not its position")
    if DISPOSABLE_SUFFIX_RE.search(name):
        tics.append("versioned/disposable suffix - rename or replace, never fork the name")
    if PUFFERY_PREFIX_RE.match(name):
        tics.append("puffery prefix - adjectives describe nothing a selector needs")
    if len(segments) > MAX_SEGMENTS:
        tics.append(f"{len(segments)} hyphen segments - a human name is a noun, not a sentence")
    return tics


def nearest(unknown: str, known: frozenset[str]) -> list[str]:
    """Return the known classes an invented name is probably a duplicate OF.

    CONTAINMENT BEATS SEGMENT OVERLAP, and if any containment match exists the weaker kind is
    not shown at all - a gate that gives wrong advice gets muted, and a muted gate is worth
    nothing. Segment overlap still earns its place for the case containment cannot see.
    """
    contained = {name for name in known if name != unknown and (name in unknown or unknown in name)}
    if contained:
        return sorted(contained, key=lambda name: (-len(name), name))[:3]

    segments = set(unknown.split("-"))
    overlapping = {name for name in known if name != unknown and set(name.split("-")) & segments}
    return sorted(overlapping, key=lambda name: (-len(name), name))[:3]


def load_vocab(path: Path) -> tuple[frozenset[str], dict[str, str], list[str]]:
    """Read the vocabulary manifest. Returns (vocabulary, additions, failures)."""
    if tomllib is None:
        return frozenset(), {}, ["Python 3.11+ required (tomllib) - upgrade the interpreter"]
    if not path.is_file():
        return (
            frozenset(),
            {},
            [f"vocabulary manifest missing: {path} - run `slopgate.py init` and commit it"],
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        return frozenset(), {}, [f"{path} is not valid TOML ({exc}); failing closed"]
    vocabulary = frozenset(data.get("vocabulary", {}).get("classes", []))
    additions: dict[str, str] = {}
    failures: list[str] = []
    for name, reason in data.get("additions", {}).items():
        # The written reason IS the mechanism; an empty one is a silent bypass, so fail closed.
        if not isinstance(reason, str) or not reason.strip():
            failures.append(
                f"addition `.{name}` in {path.name} has no written reason - the reason is the "
                f"whole cost of the escape hatch; add one or remove the entry"
            )
            continue
        additions[str(name)] = reason
    return vocabulary, additions, failures


def write_vocab(path: Path, classes: set[str]) -> None:
    """Write the initial harvested manifest (init-time only; the gate never rewrites it)."""
    lines = [
        "# anti-slop-gates vocabulary manifest.",
        "# [vocabulary].classes is the grandfathered set harvested at init - the names this",
        "# project already wears. A NEW class must either reuse one of these or be declared",
        "# under [additions] with a reason. The gate NEVER regenerates this file: adding a",
        "# name is a deliberate, reviewable act, and that cost is the mechanism.",
        "",
        "[vocabulary]",
        "classes = [",
    ]
    lines.extend(f'  "{name}",' for name in sorted(classes))
    lines.append("]")
    lines.append("")
    lines.append("[additions]")
    lines.append('# "hero-band" = "new landing section approved by the owner; no existing name fits"')
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def check(root: Path, html_paths: list[Path], vocab_path: Path) -> NamingVerdict:
    """Judge the tree's emitted classes against the frozen vocabulary."""
    vocabulary, additions, failures = load_vocab(vocab_path)
    if failures:
        return NamingVerdict(failures, [])

    allowed = vocabulary | frozenset(additions)
    notes: list[str] = []
    emitted: set[str] = set()

    for path in html_paths:
        rel = path.relative_to(root).as_posix()
        found = classes_in(path)
        emitted |= found
        for unknown in sorted(found - allowed):
            hint = nearest(unknown, allowed)
            if hint:
                failures.append(
                    f"{rel} emits `.{unknown}`, which is NOT project vocabulary - this project "
                    f"already names this: {', '.join(f'`.{h}`' for h in hint)}. Use the existing "
                    f"class. A parallel vocabulary beside an existing one IS the slop."
                )
            else:
                failures.append(
                    f"{rel} emits `.{unknown}`, which is NOT project vocabulary. If nothing "
                    f"existing fits, declare it in {vocab_path.name} under [additions] with a "
                    f"reason - deliberately, so the next reader knows why it exists."
                )
            for tic in ai_tics(unknown):
                failures.append(f"{rel} `.{unknown}`: {tic}")

    # Grandfathered tics are debt to pay down, not a red gate - but they stay visible.
    for name in sorted(emitted & allowed):
        for tic in ai_tics(name):
            notes.append(f"grandfathered `.{name}`: {tic}")

    # The escape hatch must not silently widen: a permission nobody uses is permission to drift.
    notes.extend(
        f"declared addition `.{stale}` is no longer emitted - drop it from {vocab_path.name}"
        for stale in sorted(set(additions) - emitted)
    )
    return NamingVerdict(failures, notes)
