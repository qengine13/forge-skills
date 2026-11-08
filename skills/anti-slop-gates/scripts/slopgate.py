#!/usr/bin/env python3
# Modified for Forge Skills: portable public review guidance; see collection NOTICE.
"""slopgate — mechanical anti-slop gates for any code tree. Stdlib-only, Python 3.11+.

Web gates (templates and stylesheets):

  dom      DOM/CSS structure debt (depth, wrapper chains, inline style/script, utility soup,
           selector complexity, hardcoded layout px) - baseline-ratcheted per file.
  naming   class vocabulary - a new class must reuse the project's own names or be declared
           with a reason; machine-name tics (content-wrapper-inner, box2, -v2) fail on sight.
  ratchet  tree-wide counters for grandfathered debt (inline handlers, &nbsp; layout, <br>
           stacking, z-index arms race) - the counters only turn DOWN.

Code gates (any language; Python and Node/TypeScript adapters built in) — see gate_code.py and
adapters.py: format, lint, complexity budget, security, type errors, silenced warnings without a
reason, docstrings with purpose + example, duplicate blocks, prior-art receipts, layout map,
shared-helper register, one manifest per change. Every one is a per-file count that only shrinks.

Commands:
    slopgate.py init [--root DIR] [--vendor]   # one-time: config + vocab harvest + baselines
    slopgate.py adopt [--root DIR] [--vendor]  # init on an existing codebase + ranked clean-up plan
    slopgate.py check [--root DIR] [--base REF]  # the gate: exit 1 on any regression
    slopgate.py report [--root DIR]            # ranked debt audit, no exit code
    slopgate.py update [--root DIR]            # ratchet baselines DOWN to current counts
    slopgate.py target [--root DIR]            # what separates the module map from the target map
    slopgate.py vocab [--root DIR]             # print the frozen vocabulary

The engine lives in the skill; only `.slopgates/` (config + vocab + baselines) lives in the
project - commit that directory. `init --vendor` additionally copies the engine into
`tools/slopgates/` for CI runners that cannot see the skill directory.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    sys.stderr.write("slopgate needs Python 3.11+ (tomllib)\n")
    sys.exit(2)

import tomllib

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import adapters
import gate_boundary
import gate_code
import gate_dom
import gate_naming
import gate_ratchet

CONFIG_DIR = ".slopgates"
CONFIG_FILE = "config.toml"
VOCAB_FILE = "vocab.toml"
DOM_BASELINES = "dom_baselines.json"
RATCHET_BASELINES = "ratchet_baselines.json"

DEFAULT_PRUNE = (
    "node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build", ".next",
    ".nuxt", ".output", ".svelte-kit", "coverage", "vendor", ".worktrees", ".slopgates", "slopgates",
)
DEFAULT_HTML_GLOBS = ("**/*.html", "**/*.htm")
DEFAULT_CSS_GLOBS = ("**/*.css", "**/*.scss")
# The naming gate reads class/className literals from component formats the DOM parser cannot
# structurally parse; naming globs therefore include component file formats.
DEFAULT_NAMING_GLOBS = (
    "**/*.html", "**/*.htm", "**/*.jsx", "**/*.tsx", "**/*.vue", "**/*.svelte", "**/*.astro",
)

CODE_BASELINES = "code_baselines.json"
MODULE_MAP = "modules.toml"
TARGET_MAP = "modules.target.toml"
CLEANUP_PLAN = "CLEANUP-PLAN.md"
ENGINE_FILES = (
    "slopgate.py", "gate_dom.py", "gate_naming.py", "gate_ratchet.py", "textstrip.py",
    "gate_code.py", "gate_boundary.py", "adapters.py",
)
# An explicit project marker prevents installing a second quality-gate system.
NATIVE_GATE_MARKERS = (".native-quality-gates",)
DEFAULT_SOURCE_GLOBS = (
    "**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs", "**/*.mts", "**/*.cts",
)
DEFAULT_FORBIDDEN_FILES = (
    "utils.py", "helpers.py", "funcs.py", "misc.py", "utils.ts", "helpers.ts", "utils.js", "helpers.js",
)
# Clean-up plan order: the checks whose debt hurts most come first.
CHECK_WEIGHT = {"security": 5, "types": 4, "complexity": 3, "duplicate": 3, "helper": 3, "boundary": 3,
                "silenced": 2, "lint": 2, "prior-art": 2, "layout": 1, "docstring": 1, "format": 1}


def load_config(root: Path) -> dict:
    """Read the project's .slopgates/config.toml, failing closed when absent or corrupt."""
    path = root / CONFIG_DIR / CONFIG_FILE
    if not path.is_file():
        sys.stderr.write(f"slopgate: no {path.relative_to(root)} - run `slopgate.py init` first\n")
        sys.exit(1)
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        sys.stderr.write(f"slopgate: {path} is not valid TOML ({exc}); failing closed\n")
        sys.exit(1)


def discover(root: Path, globs: tuple[str, ...], prune: set[str]) -> list[Path]:
    """Return in-scope source files in deterministic order, pruned and deduped.

    The suffix filter is DERIVED from the configured globs (a config that says `**/*.tsx` means
    tsx files are in scope) - a hardcoded suffix set silently dropped what the config asked for.
    """
    allowed = {Path(pattern).suffix.lower() for pattern in globs if Path(pattern).suffix}
    seen: set[Path] = set()
    result: list[Path] = []
    for pattern in globs:
        for path in sorted(root.glob(pattern)):
            if path in seen or not path.is_file():
                continue
            if allowed and path.suffix.lower() not in allowed:
                continue
            seen.add(path)
            parts = path.relative_to(root).parts
            if any(part in prune for part in parts):
                continue
            result.append(path)
    return result


def gather(
    root: Path, config: dict
) -> tuple[list[Path], list[Path], list[Path], gate_dom.DomBudgets]:
    """Resolve the scan file sets and budgets one time for every gate."""
    scan = config.get("scan", {})
    prune = set(scan.get("prune", DEFAULT_PRUNE))
    html_globs = tuple(scan.get("html_globs", DEFAULT_HTML_GLOBS))
    css_globs = tuple(scan.get("css_globs", DEFAULT_CSS_GLOBS))
    naming_globs = tuple(scan.get("naming_globs", DEFAULT_NAMING_GLOBS))
    html_paths = discover(root, html_globs, prune)
    css_paths = discover(root, css_globs, prune)
    naming_paths = discover(root, naming_globs, prune)
    raw = config.get("budgets", {})
    budgets = gate_dom.DomBudgets(
        depth=int(raw.get("dom_depth", 10)),
        wrapper_chain=int(raw.get("wrapper_chain", 3)),
        selector_parts=int(raw.get("selector_parts", 4)),
        selector_classes=int(raw.get("selector_classes", 5)),
        utility_classes_forbidden=bool(raw.get("utility_classes_forbidden", True)),
    )
    return html_paths, css_paths, naming_paths, budgets


def _dedupe(*path_lists: list[Path]) -> list[Path]:
    """One flat, ordered, duplicate-free file list for the ratchet gate's suffix scoping."""
    seen: set[Path] = set()
    result: list[Path] = []
    for paths in path_lists:
        for path in paths:
            if path not in seen:
                seen.add(path)
                result.append(path)
    return result


def _uses_tailwind(root: Path) -> bool:
    """Detect a Tailwind project - utility classes are its idiom, not slop, there."""
    if any(root.glob("tailwind.config.*")):
        return True
    package = root / "package.json"
    return package.is_file() and '"tailwindcss"' in package.read_text(
        encoding="utf-8", errors="replace"
    )


def _default_config_text(utility_forbidden: bool) -> str:
    """Render the initial config.toml (TOML literal strings keep regexes unescaped)."""
    lines = [
        "# anti-slop-gates project config. Committed; edited deliberately.",
        "",
        "[scan]",
        f"html_globs = {list(DEFAULT_HTML_GLOBS)!r}".replace("'", '"'),
        f"css_globs = {list(DEFAULT_CSS_GLOBS)!r}".replace("'", '"'),
        f"naming_globs = {list(DEFAULT_NAMING_GLOBS)!r}".replace("'", '"'),
        f"prune = {list(DEFAULT_PRUNE)!r}".replace("'", '"'),
        "",
        "[budgets]",
        "dom_depth = 10",
        "wrapper_chain = 3",
        "selector_parts = 4",
        "selector_classes = 5",
        f"utility_classes_forbidden = {str(utility_forbidden).lower()}",
    ]
    for entry in gate_ratchet.DEFAULT_RATCHETS:
        lines.append("")
        lines.append("[[ratchet]]")
        lines.append(f'name = "{entry["name"]}"')
        lines.append(f'description = "{entry["description"]}"')
        lines.append(f'remedy = "{entry["remedy"]}"')
        lines.append(f"suffixes = {list(entry['suffixes'])!r}".replace("'", '"'))
        lines.append(f"pattern = '{entry['pattern']}'")
    lines.append("")
    return "\n".join(lines)


def native_gates(root: Path) -> str | None:
    """Return the marker proving this repository runs its own native gates, or None."""
    candidates = [root, *root.parents]
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
    )
    top = Path(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip() else root
    for base in candidates:
        for marker in NATIVE_GATE_MARKERS:
            if (base / marker).is_file():
                return str(base / marker)
        if base == top:
            break
    return None


def _code_settings(config: dict) -> dict:
    """Read the [code] section with defaults (absent section = the code pack is not installed)."""
    code = dict(config.get("code", {}))
    code.setdefault("source_globs", list(DEFAULT_SOURCE_GLOBS))
    code.setdefault("docs_globs", ["**/*.md"])
    code.setdefault("tools", [])
    code.setdefault("tools_missing_at_init", [])
    code.setdefault("forbidden_files", list(DEFAULT_FORBIDDEN_FILES))
    code.setdefault("source_roots", [])
    code.setdefault("duplicate_min_lines", 6)
    code.setdefault("checks_off", [])
    return code


def _detect_languages(root: Path, sources: list[Path]) -> set[str]:
    suffixes = {path.suffix for path in sources}
    languages = set()
    if suffixes & gate_code.PY_SUFFIXES:
        languages.add("python")
    if suffixes & gate_code.JS_SUFFIXES or (root / "package.json").is_file():
        languages.add("node")
    return languages


def collect_code(root: Path, config: dict) -> tuple[dict[str, dict[str, list[str]]], list[gate_code.Finding]]:
    """Run every code check; return per-check, per-file messages plus NOT RUN findings."""
    code = _code_settings(config)
    prune = set(config.get("scan", {}).get("prune", DEFAULT_PRUNE))
    sources = discover(root, tuple(code["source_globs"]), prune)
    counts: dict[str, dict[str, list[str]]] = {
        name: {} for name in ("silenced", "docstring", "layout", "helper", "duplicate", "prior-art", "boundary")
    }
    map_data, map_errors = gate_boundary.load_map(root / CONFIG_DIR / MODULE_MAP)
    if "code" in config and not (root / CONFIG_DIR / MODULE_MAP).is_file():
        map_errors.append(f"{MODULE_MAP} is missing - the module map is part of the installed pack")
    texts: dict[str, str] = {}
    forbidden = set(code["forbidden_files"])
    for path in sources:
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        is_test = gate_code.is_test_path(rel)
        if not is_test:
            texts[rel] = text
        boundary, module_layout = gate_boundary.boundary_gaps(root, rel, text, map_data)
        per_file = {
            "boundary": boundary,
            "silenced": [
                f"line {n}: silenced with no reason"
                for n in gate_code.count_silenced(text, python=path.suffix in gate_code.PY_SUFFIXES)
            ],
            "layout": gate_code.layout_gaps(rel, forbidden, code["source_roots"]) + module_layout,
            "helper": gate_code.helper_gaps(rel, text, config.get("helper", [])),
            "docstring": []
            if is_test or path.name.endswith(".d.ts")
            else gate_code.python_docstring_gaps(text)
            if path.suffix in gate_code.PY_SUFFIXES
            else gate_code.ts_docstring_gaps(text),
        }
        for check, messages in per_file.items():
            if messages:
                counts[check][rel] = messages
    counts["duplicate"] = gate_code.duplicate_blocks(texts, int(code["duplicate_min_lines"]))
    docs = discover(root, tuple(code["docs_globs"]), prune)
    by_name: dict[str, list[Path]] = {}
    for path in discover(root, ("**/*",), prune):
        by_name.setdefault(path.name, []).append(path.resolve())
    for path in docs:
        gaps = gate_code.prior_art_gaps(root, path.read_text(encoding="utf-8", errors="replace"), by_name)
        if gaps:
            counts["prior-art"][path.relative_to(root).as_posix()] = gaps
    for check in code["checks_off"]:
        counts.pop(check, None)

    findings = [gate_code.Finding("module-map", "fail", error) for error in map_errors]
    raw_budgets = config.get("budgets", {})
    budgets = adapters.Budgets(
        complexity=int(raw_budgets.get("complexity", 10)),
        statements=int(raw_budgets.get("statements", 50)),
        args=int(raw_budgets.get("args", 5)),
    )
    for tool in code["tools"]:
        if tool not in adapters.TOOLS:
            findings.append(gate_code.Finding(tool, "fail", f"unknown tool `{tool}` in [code].tools"))
            continue
        try:
            produced = adapters.TOOLS[tool][1](root, budgets)
        except adapters.ToolNotRunError as exc:
            findings.append(gate_code.Finding(tool, "fail", f"NOT RUN — {exc}. Enabled at init, so this fails closed."))
            continue
        for check, files in produced.items():
            bucket = counts.setdefault(check, {})
            for rel, messages in files.items():
                if prune & set(rel.split("/")):
                    continue  # the same pruned folders as every other check (vendored engine, deps)
                bucket.setdefault(rel, []).extend(messages)
    for reason in code["tools_missing_at_init"]:
        findings.append(gate_code.Finding("tools", "notrun", f"NOT RUN — {reason}"))
    return counts, findings


def check_manifest(root: Path, config: dict, base: str | None) -> list[gate_code.Finding]:
    """One manifest per change: when required, a change against `base` adds exactly one manifest."""
    change = config.get("code", {}).get("change", {})
    if not change.get("require_manifest"):
        return []
    if not base:
        return [gate_code.Finding("manifest", "fail", "one-manifest-per-change is required - pass --base REF")]
    folder = change.get("manifest_dir", "docs/changes").rstrip("/") + "/"
    diff = subprocess.run(["git", "-C", str(root), "diff", "--name-only", "--diff-filter=AMR", base], capture_output=True, text=True, check=False)
    if diff.returncode != 0:
        return [gate_code.Finding("manifest", "fail", f"git diff against {base} failed: {diff.stderr.strip()}")]
    untracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"], capture_output=True, text=True, check=False
    ).stdout
    changed = set((diff.stdout + untracked).split())
    manifests = sorted(path for path in changed if path.startswith(folder))
    if len(manifests) == 1 or not changed:
        return []
    return [gate_code.Finding("manifest", "fail", f"a change carries exactly one manifest in {folder}; found {manifests or 'none'}")]


def _code_config_text(languages: set[str], tools: list[str], missing: list[str]) -> str:
    """Render the [code] section appended to config.toml."""
    def toml_list(values: list[str]) -> str:
        return "[" + ", ".join('"' + v.replace('"', "'") + '"' for v in values) + "]"

    return "\n".join([
        "",
        "# ---- code pack (any language). Edited deliberately; see references/RULES.md ----",
        "[code]",
        f"languages = {toml_list(sorted(languages))}",
        f"source_globs = {toml_list(list(DEFAULT_SOURCE_GLOBS))}",
        'docs_globs = ["**/*.md"]',
        "# A tool listed here that cannot run FAILS the gate. Install a missing one, then add it.",
        f"tools = {toml_list(tools)}",
        f"tools_missing_at_init = {toml_list(missing)}",
        f"forbidden_files = {toml_list(list(DEFAULT_FORBIDDEN_FILES))}",
        "# The layout map: where source may live (empty = anywhere). e.g. [\"src\", \"app\"]",
        "source_roots = []",
        "duplicate_min_lines = 6",
        "checks_off = []",
        "",
        "[code.change]",
        "# One manifest per change: `check --base main` then requires exactly one new/edited file here.",
        "require_manifest = false",
        'manifest_dir = "docs/changes"',
        "",
        "# The shared-helper register: a concern with one canonical home. A new copy elsewhere fails.",
        "# [[helper]]",
        '# name = "json-envelope"',
        '# canonical = "src/http/respond.py"',
        "# pattern = 'JsonResponse\\(\\{\\s*\"ok\"'",
        '# remedy = "respond_ok() / respond_error()"',
        "",
    ]) + "\n"


def _code_budgets_text() -> str:
    return "complexity = 10\nstatements = 50\nargs = 5\n"


def write_cleanup_plan(root: Path, counts: dict[str, dict[str, list[str]]], findings: list[gate_code.Finding]) -> Path:
    """Write the ranked clean-up plan: the files with the heaviest frozen debt first."""
    totals: dict[str, dict[str, int]] = {}
    for check, files in counts.items():
        for rel, messages in files.items():
            totals.setdefault(rel, {})[check] = len(messages)
    ranked = sorted(
        totals.items(),
        key=lambda item: (-sum(CHECK_WEIGHT.get(c, 1) * n for c, n in item[1].items()), item[0]),
    )
    lines = [
        "# Clean-up plan",
        "",
        "Written by `slopgate.py adopt`. Today's counts are each file's starting limit: nothing here",
        "blocks today, nothing may grow, and every fix lowers the limit for good (`slopgate.py update`).",
        "Worst first — weighted by what hurts most (security, type errors, complexity, duplicates).",
        "",
        "| Rank | File | Weighted | What is frozen |",
        "|---:|---|---:|---|",
    ]
    for rank, (rel, by_check) in enumerate(ranked, start=1):
        score = sum(CHECK_WEIGHT.get(c, 1) * n for c, n in by_check.items())
        detail = ", ".join(f"{c} {n}" for c, n in sorted(by_check.items(), key=lambda kv: -CHECK_WEIGHT.get(kv[0], 1)))
        lines.append(f"| {rank} | `{rel}` | {score} | {detail} |")
    if not ranked:
        lines.append("| - | nothing frozen | 0 | clean on day one |")
    not_run = [f for f in findings if f.severity in {"notrun", "fail"}]
    if not_run:
        lines += ["", "## Checks that did not run (install, then add to `[code].tools`)", ""]
        lines += [f"- {f.check}: {f.message}" for f in not_run]
    lines += [
        "",
        "## Audit findings",
        "",
        "The mechanical counts above miss what no counter sees. Perform the code review in the skill package references/adoption-review.md",
        "(static analysis, duplicate and dead code, circular imports, dependency health;",
        "keep the review read-only) and add ranked findings here. For security",
        "or critical-path risk, include a read-only security review. Then work this file top-down.",
        "",
        "## Structure: as-is map and target map",
        "",
        "`.slopgates/modules.toml` is the AS-IS map, drafted from today's folders and imports and",
        "enforced from day one, so no boundary gets worse. Perform the architecture review in references/adoption-review.md to",
        "study how the project is designed and built, confirm the as-is map, and write two files:",
        "",
        "- `.slopgates/modules.target.toml` — the TARGET map (same format), never enforced directly;",
        "- `.slopgates/TARGET-MAP.md` — each move with its reason, and a migration path in small,",
        "  separately shippable steps, ranked by gain against risk.",
        "",
        "For HTML-only projects with no importable modules, record architecture review as not applicable;",
        "do not invent module maps or migrations. Nothing restructures by itself.",
        "The owner approves each migration step; when its code lands, the",
        "enforced map is edited to match that step. `slopgate.py target` shows what remains.",
        "",
    ]
    path = root / CONFIG_DIR / CLEANUP_PLAN
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _install_code_pack(root: Path, config_path: Path) -> tuple[dict, list[gate_code.Finding]]:
    """Append the [code] section, freeze today's code counts, and return (counts, findings)."""
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    prune = set(config.get("scan", {}).get("prune", DEFAULT_PRUNE))
    sources = discover(root, DEFAULT_SOURCE_GLOBS, prune)
    languages = _detect_languages(root, sources)
    tools, missing = adapters.detect(root, languages)
    text = config_path.read_text(encoding="utf-8")
    text = text.replace("[budgets]\n", "[budgets]\n" + _code_budgets_text(), 1)
    text += _code_config_text(languages, tools, missing)
    map_path = root / CONFIG_DIR / MODULE_MAP
    drafted = not map_path.exists()
    if drafted:
        rels = [path.relative_to(root).as_posix() for path in sources]
        texts = {rel: (root / rel).read_text(encoding="utf-8", errors="replace") for rel in rels}
        map_path.write_text(gate_boundary.draft_map(root, rels, texts), encoding="utf-8")
    counts, findings = collect_code(root, tomllib.loads(text))
    if any(f.severity == "fail" for f in findings):
        if drafted:
            map_path.unlink()
        return counts, findings  # never freeze a baseline over a tool that crashed; config untouched
    config_path.write_text(text, encoding="utf-8")
    gate_code.ratchet_down(counts, {}, root / CONFIG_DIR / CODE_BASELINES, bootstrap=True)
    if drafted and not sources:
        print("  no importable source modules found; architecture map review is not applicable")
    elif drafted:
        print(f"  module map DRAFTED -> {map_path.relative_to(root)} - perform the bundled adoption architecture review to confirm it")
    print(f"  code pack: languages {sorted(languages) or 'none found'}; tools {tools or 'none'}")
    for reason in missing:
        print(f"  NOT RUN at init — {reason}")
    frozen = sum(len(files) for files in counts.values())
    print(f"  code debt frozen as per-file limits across {frozen} file/check pair(s)")
    return counts, findings


def cmd_init(root: Path, vendor: bool, adopt: bool = False) -> int:
    """Bootstrap a project: config, vocabulary harvest, all baselines, wiring snippet.

    On a project initialised before the code pack existed (config present, no code baselines),
    init adds the code pack only — the web baselines are never re-harvested.
    """
    gates_dir = root / CONFIG_DIR
    config_path = gates_dir / CONFIG_FILE
    vocab_path = gates_dir / VOCAB_FILE
    code_only = config_path.exists() and not (gates_dir / CODE_BASELINES).exists() and "[code]" not in config_path.read_text(encoding="utf-8")
    if (config_path.exists() or vocab_path.exists()) and not code_only:
        sys.stderr.write(
            "slopgate: .slopgates/ already initialized - init never reruns (a re-harvest would "
            "grandfather new drift). Edit config.toml/vocab.toml deliberately instead.\n"
        )
        return 1
    gates_dir.mkdir(parents=True, exist_ok=True)

    if not code_only:
        _init_web(root, gates_dir, config_path, vocab_path)
    if vendor:  # before the freeze, so the copied engine is not born as new debt
        target = root / "tools" / "slopgates"
        target.mkdir(parents=True, exist_ok=True)
        for name in ENGINE_FILES:
            shutil.copy2(SCRIPT_DIR / name, target / name)
        print(f"vendored engine -> {target.relative_to(root)}/")
    counts, findings = _install_code_pack(root, config_path)
    hard = [f for f in findings if f.severity == "fail"]
    for finding in hard:
        print(f"code fail: [{finding.check}] {finding.message}")
    if hard:
        print("slopgate: a tool crashed during init - code pack NOT installed. Fix it, then run init again.")
        return 1

    if adopt:
        plan = write_cleanup_plan(root, counts, findings)
        print(f"ranked clean-up plan -> {plan.relative_to(root)} (worst first)")
        print("next: perform the bundled adoption code review and add its findings to that plan's Audit findings section")

    print(f"initialized {gates_dir.relative_to(root)}/ - commit the whole directory")
    print()
    print("wire it (pre-commit and CI):")
    engine = "tools/slopgates/slopgate.py" if vendor else str(SCRIPT_DIR / "slopgate.py")
    print(f"  python3 {engine} check")
    return 0


def _init_web(root: Path, gates_dir: Path, config_path: Path, vocab_path: Path) -> None:
    """The original web bootstrap: config, vocabulary harvest, DOM and ratchet baselines."""
    utility_forbidden = not _uses_tailwind(root)
    config_path.write_text(_default_config_text(utility_forbidden), encoding="utf-8")
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    html_paths, css_paths, naming_paths, budgets = gather(root, config)

    gate_naming.write_vocab(vocab_path, gate_naming.harvest(root, naming_paths))

    dom_findings = gate_dom.update_baselines(
        root, html_paths, css_paths, budgets, gates_dir / DOM_BASELINES
    )
    ratchets, ratchet_errors = gate_ratchet.ratchets_from_config(config.get("ratchet", []))
    ratchet_findings = gate_ratchet.update_baselines(
        root, _dedupe(html_paths, css_paths, naming_paths), ratchets, gates_dir / RATCHET_BASELINES
    )
    # A malformed allow marker is never baselined - surface it now, before the first check reds.
    for finding in gate_dom.bare_allow_failures(root, _dedupe(html_paths, css_paths)):
        print(f"WARNING {finding.key}: {finding.message} - `check` will fail until fixed")
    print(f"  web: scanned {len(html_paths)} template(s), {len(css_paths)} stylesheet(s); utility classes "
          f"{'forbidden' if utility_forbidden else 'allowed (Tailwind detected)'}")
    frozen = sum(1 for f in dom_findings + ratchet_findings if f.severity == "notice")
    print(f"  web debt frozen across {frozen} baseline(s) - new files are born clean")
    for finding in ratchet_errors:
        print(f"  ratchet config error: [{finding.ratchet}] {finding.message}")


def _check_web(root: Path, config: dict) -> int:
    gates_dir = root / CONFIG_DIR
    html_paths, css_paths, naming_paths, budgets = gather(root, config)
    failures = 0
    dom_findings = gate_dom.check(
        root, html_paths, css_paths, budgets, gates_dir / DOM_BASELINES
    ) + gate_dom.bare_allow_failures(root, _dedupe(html_paths, css_paths))
    for finding in dom_findings:
        print(f"dom {finding.severity}: [{finding.key}] {finding.message}")
    failures += sum(1 for f in dom_findings if f.severity == "fail")

    verdict = gate_naming.check(root, naming_paths, gates_dir / VOCAB_FILE)
    for note in verdict.notes:
        print(f"naming note: {note}")
    for failure in verdict.failures:
        print(f"naming fail: {failure}")
    failures += len(verdict.failures)

    ratchets, ratchet_errors = gate_ratchet.ratchets_from_config(config.get("ratchet", []))
    ratchet_findings = ratchet_errors + gate_ratchet.check(
        root, _dedupe(html_paths, css_paths, naming_paths), ratchets, gates_dir / RATCHET_BASELINES
    )
    for finding in ratchet_findings:
        print(f"ratchet {finding.severity}: [{finding.ratchet}] {finding.message}")
    failures += sum(1 for f in ratchet_findings if f.severity == "fail")
    return failures


def cmd_check(root: Path, base: str | None = None) -> int:
    """Run every gate; exit 1 on any regression."""
    config = load_config(root)
    failures = _check_web(root, config)
    if "code" in config:
        baselines, findings = gate_code.load_baselines(root / CONFIG_DIR / CODE_BASELINES)
        counts, tool_findings = collect_code(root, config)
        findings += tool_findings + gate_code.compare(counts, baselines) + check_manifest(root, config, base)
        for finding in findings:
            print(f"code {finding.severity}: [{finding.check}] {finding.message}")
        failures += sum(1 for f in findings if f.severity == "fail")
    else:
        print("code note: no [code] section - run `slopgate.py init` to add the code pack")

    if failures:
        print(f"\nslopgate: {failures} failure(s) - the tree got worse, not different")
        return 1
    print("slopgate: clean - no regression against the committed baselines")
    return 0


def cmd_report(root: Path) -> int:
    """Ranked audit of current debt; informational, never fails."""
    config = load_config(root)
    html_paths, css_paths, _naming_paths, budgets = gather(root, config)
    print(gate_dom.render_report(root, html_paths, css_paths, budgets))
    return 0


def cmd_update(root: Path) -> int:
    """Ratchet every baseline DOWN to the current counts; refuses raises."""
    config = load_config(root)
    gates_dir = root / CONFIG_DIR
    html_paths, css_paths, naming_paths, budgets = gather(root, config)

    findings = gate_dom.update_baselines(
        root, html_paths, css_paths, budgets, gates_dir / DOM_BASELINES
    ) + gate_dom.bare_allow_failures(root, _dedupe(html_paths, css_paths))
    ratchets, ratchet_errors = gate_ratchet.ratchets_from_config(config.get("ratchet", []))
    findings2 = ratchet_errors + gate_ratchet.update_baselines(
        root, _dedupe(html_paths, css_paths, naming_paths), ratchets, gates_dir / RATCHET_BASELINES
    )
    failed = False
    for finding in findings:
        print(f"dom {finding.severity}: [{finding.key}] {finding.message}")
        failed = failed or finding.severity == "fail"
    for finding in findings2:
        print(f"ratchet {finding.severity}: [{finding.ratchet}] {finding.message}")
        failed = failed or finding.severity == "fail"
    if "code" in config:
        path = gates_dir / CODE_BASELINES
        baselines, code_findings = gate_code.load_baselines(path)
        if not code_findings:
            counts, tool_findings = collect_code(root, config)
            code_findings = [f for f in tool_findings if f.severity == "fail"]
            if not code_findings:  # a failed tool shares buckets with others: never lower blind
                code_findings = gate_code.ratchet_down(counts, baselines, path, bootstrap=False)
        for finding in code_findings:
            print(f"code {finding.severity}: [{finding.check}] {finding.message}")
            failed = failed or finding.severity == "fail"
        if not code_findings:
            print("code: limits lowered to today's counts (checks that did not run kept theirs)")
    return 1 if failed else 0


def cmd_target(root: Path) -> int:
    """Print what still separates the enforced module map from the target map (never moves code)."""
    current, errors = gate_boundary.load_map(root / CONFIG_DIR / MODULE_MAP)
    target, target_errors = gate_boundary.load_map(root / CONFIG_DIR / TARGET_MAP)
    for error in errors + target_errors:
        print(f"module-map fail: {error}")
    if errors or target_errors:
        return 1
    if not target and not current.get("module") and not discover(root, DEFAULT_SOURCE_GLOBS, set(DEFAULT_PRUNE)):
        print("no importable source modules found; no target map is required")
        return 0
    if not target:
        print(f"no {TARGET_MAP} - complete the bundled adoption architecture review to write one")
        return 0
    gaps = gate_boundary.map_differences(current, target)
    print(f"target map: {len(gaps)} difference(s) remain" + (":" if gaps else " - the target is the enforced map"))
    for gap in gaps:
        print(f"  - {gap}")
    return 0


def cmd_vocab(root: Path) -> int:
    """Print the frozen vocabulary and declared additions."""
    gates_dir = root / CONFIG_DIR
    vocabulary, additions, failures = gate_naming.load_vocab(gates_dir / VOCAB_FILE)
    for failure in failures:
        print(f"naming fail: {failure}")
        return 1
    print(f"vocabulary ({len(vocabulary)} classes):")
    print("  " + ", ".join(f".{name}" for name in sorted(vocabulary)))
    if additions:
        print("declared additions:")
        for name, reason in sorted(additions.items()):
            print(f"  .{name} - {reason}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "adopt", "check", "report", "update", "vocab", "target"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--vendor",
        action="store_true",
        help="init/adopt: copy the engine into tools/slopgates/ for CI runners",
    )
    parser.add_argument("--base", help="check: git ref the change is measured against (manifest rule)")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    marker = native_gates(root)
    if marker:
        sys.stderr.write(
            f"slopgate: refusing to run - this repository has its own native gates ({marker}). "
            "Use those; never double-gate.\n"
        )
        return 2
    if args.command in {"init", "adopt"}:
        return cmd_init(root, args.vendor, adopt=args.command == "adopt")
    if args.command == "check":
        return cmd_check(root, args.base)
    if args.command == "report":
        return cmd_report(root)
    if args.command == "update":
        return cmd_update(root)
    if args.command == "target":
        return cmd_target(root)
    return cmd_vocab(root)


if __name__ == "__main__":
    raise SystemExit(main())
