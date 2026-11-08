"""Module map — which folder owns which area, its file layout, and which way imports may point.
# Modified for Forge Skills: portable public review guidance; see collection NOTICE.

The map lives in the project's `.slopgates/modules.toml`. An architecture reviewer writes it
(owner, responsibility, public entry files, allowed dependencies, file layout); `init`/`adopt` only
draft a starting map from today's folders and imports so day one passes and the architect can
tighten it. Generalised from import-linter style contracts (`forbidden` + `independence` in
pyproject.toml) without the third-party dependency, for Python and JS/TS alike.

Two findings, both per-file counts that ride the same only-shrink ratchet as every other check:
  boundary  an import from module A into module B when B is not in A's `may_depend_on`
            (wrong direction), or into a file of B that is not in B's `public` list (a reach-in
            to another module's internals).
  layout    a file inside a module that matches none of the module's declared `layout` globs.

Known limits, stated: dynamic imports with computed names, `sys.path` tricks and bundler aliases
not declared under [map.aliases] are invisible; an unresolvable import is ignored, not guessed.
"""

from __future__ import annotations

import ast
import fnmatch
import re
import tomllib
from pathlib import Path, PurePosixPath

from gate_code import JS_SUFFIXES, PY_SUFFIXES, is_test_path

JS_IMPORT_RE = re.compile(
    r"""(?:^|\s)(?:import|export)\s[^'"`;]*?from\s*['"](?P<a>[^'"]+)['"]"""
    r"""|^\s*import\s*['"](?P<b>[^'"]+)['"]"""
    r"""|(?:import|require)\s*\(\s*['"](?P<c>[^'"]+)['"]\s*\)""",
    re.MULTILINE,
)
JS_RESOLVE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
ENTRY_FILES = ("__init__.py", "index.ts", "index.tsx", "index.js", "index.mjs")


def load_map(path: Path) -> tuple[dict, list[str]]:
    """Return the parsed module map and config errors (fail closed on a malformed map)."""
    if not path.is_file():
        return {}, []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        return {}, [f"{path.name} is not valid TOML ({exc})"]
    errors = []
    names = set()
    for module in data.get("module", []):
        if not module.get("name") or not module.get("path"):
            errors.append(f"a [[module]] needs name and path: {module}")
        names.add(module.get("name"))
    for module in data.get("module", []):
        unknown = set(module.get("may_depend_on", [])) - names
        if unknown:
            errors.append(f"module {module.get('name')} depends on unknown module(s) {sorted(unknown)}")
    return data, errors


def _module_of(rel: str, modules: list[dict]) -> dict | None:
    best = None
    for module in modules:
        base = module["path"].rstrip("/")
        if (rel == base or rel.startswith(base + "/")) and (best is None or len(base) > len(best["path"])):
            best = module
    return best


def _python_targets(root: Path, rel: str, text: str, python_roots: list[str]) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found: list[tuple[int, str]] = []
    package = PurePosixPath(rel).parent
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:  # every name is its own import: check each one
                target = _resolve_dotted(root, alias.name, python_roots)
                if target:
                    found.append((node.lineno, target))
            continue
        if isinstance(node, ast.ImportFrom):
            stem = node.module or ""
            if node.level:
                anchor = package
                for _ in range(node.level - 1):
                    anchor = anchor.parent
                prefix = ".".join(p for p in anchor.parts if p)
                stem = ".".join(p for p in (prefix, stem) if p)
            bases = [""] if node.level else python_roots
            targets = set()
            for alias in node.names:  # `from pkg import a, b`: a submodule wins, else the package itself
                name = f"{stem}.{alias.name}" if stem else alias.name
                target = _resolve_dotted(root, name, bases) if stem else None
                target = target if target and target.count("/") >= stem.count(".") + 1 else None
                targets.add(target or _resolve_dotted(root, stem or name, bases))
            found.extend((node.lineno, t) for t in sorted(t for t in targets if t))
    return found


def _resolve_dotted(root: Path, dotted: str, bases: list[str]) -> str | None:
    parts = [p for p in dotted.split(".") if p]
    for base in bases:
        for cut in range(len(parts), 0, -1):
            stem = PurePosixPath(base, *parts[:cut]) if base else PurePosixPath(*parts[:cut])
            for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
                if (root / candidate).is_file():
                    return str(PurePosixPath(candidate))
    return None


JS_COMMENT_RE = re.compile(r"/\*.*?\*/|(?:^|(?<=\s))//[^\n]*", re.DOTALL)


def _js_targets(root: Path, rel: str, text: str, aliases: dict[str, str]) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    # Blank comments (keeping newlines so line numbers hold): a commented-out import is not one.
    text = JS_COMMENT_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    for match in JS_IMPORT_RE.finditer(text):
        spec = match.group("a") or match.group("b") or match.group("c")
        if spec.startswith("."):
            raw = (PurePosixPath(rel).parent / spec).as_posix()
        else:
            prefix = next((p for p in aliases if spec.startswith(p)), None)
            if prefix is None:
                continue  # a package import, not a path in this tree
            raw = aliases[prefix] + spec[len(prefix):]
        target = _resolve_js(root, raw)
        if target:
            found.append((text.count("\n", 0, match.start()) + 1, target))
    return found


def _resolve_js(root: Path, raw: str) -> str | None:
    normal = (root / raw).resolve()
    try:
        base = normal.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    stem = base[: -len(PurePosixPath(base).suffix)] if PurePosixPath(base).suffix in JS_RESOLVE_SUFFIXES else base
    candidates = [base] + [stem + s for s in JS_RESOLVE_SUFFIXES] + [f"{base}/index{s}" for s in JS_RESOLVE_SUFFIXES]
    return next((c for c in candidates if (root / c).is_file()), None)


def imports_of(root: Path, rel: str, text: str, map_data: dict) -> list[tuple[int, str]]:
    """Return (line, repo-relative target file) for every import that resolves inside the tree."""
    settings = map_data.get("map", {})
    suffix = PurePosixPath(rel).suffix
    if suffix in PY_SUFFIXES:
        return _python_targets(root, rel, text, settings.get("python_roots", ["", "src"]))
    if suffix in JS_SUFFIXES:
        return _js_targets(root, rel, text, settings.get("aliases", {}))
    return []


def boundary_gaps(root: Path, rel: str, text: str, map_data: dict) -> tuple[list[str], list[str]]:
    """Return (boundary messages, layout messages) for one source file."""
    modules = map_data.get("module", [])
    own = _module_of(rel, modules)
    if own is None:
        # A file outside every module (a script, a test, an entry point) may still only use a
        # module through its public entry - moving an importer out of the map cannot unlock a reach-in.
        return _reach_ins(root, rel, text, map_data, None), []
    layout: list[str] = []
    inside = rel[len(own["path"].rstrip("/")) + 1 :]
    globs = own.get("layout", [])
    if globs and not is_test_path(rel) and not any(fnmatch.fnmatch(inside, g) for g in globs):
        layout.append(f"`{inside}` is not in module {own['name']}'s file layout {globs}")
    return _reach_ins(root, rel, text, map_data, own), layout


def _reach_ins(root: Path, rel: str, text: str, map_data: dict, own: dict | None) -> list[str]:
    """Return boundary messages for one file; `own` None means the file belongs to no module."""
    modules = map_data.get("module", [])
    boundary: list[str] = []
    allowed = set(own.get("may_depend_on", [])) if own else {m["name"] for m in modules}
    for line_no, target in imports_of(root, rel, text, map_data):
        other = _module_of(target, modules)
        if other is None or (own and other["name"] == own["name"]):
            continue
        if other["name"] not in allowed:
            boundary.append(
                f"line {line_no}: {own['name'] if own else rel} may not depend on {other['name']} (imports {target})"
            )
            continue
        entry = target[len(other["path"].rstrip("/")) + 1 :]
        if not any(fnmatch.fnmatch(entry, p) for p in other.get("public", list(ENTRY_FILES))):
            boundary.append(
                f"line {line_no}: reaches into {other['name']}'s internals ({target}); "
                f"import its public entry {other.get('public', list(ENTRY_FILES))}"
            )
    return boundary


def draft_map(root: Path, sources: list[str], texts: dict[str, str]) -> str:
    """Draft modules.toml from today's tree: one module per top-level folder, observed edges allowed."""
    base = ""
    while True:  # descend through a lone wrapper folder (src/, or a single top package)
        folders = sorted({
            base + s[len(base):].split("/")[0]
            for s in sources
            if s.startswith(base) and "/" in s[len(base):] and not is_test_path(s)
        })
        folders = [f for f in folders if not f.split("/")[-1].startswith((".", "_")) and f.split("/")[-1] not in {"tests", "test", "docs", "scripts", "tools"}]
        if len(folders) != 1:
            break
        base = folders[0] + "/"
    draft: dict[str, list[dict]] = {"module": [{"name": f.split("/")[-1], "path": f, "may_depend_on": []} for f in folders]}
    for module in draft["module"]:
        entries = [e for e in ENTRY_FILES if (root / module["path"] / e).is_file()]
        module["public"] = entries or list(ENTRY_FILES)
    edges: dict[str, set[str]] = {m["name"]: set() for m in draft["module"]}
    for rel, text in texts.items():
        own = _module_of(rel, draft["module"])
        if own is None:
            continue
        for _line, target in imports_of(root, rel, text, {}):
            other = _module_of(target, draft["module"])
            if other and other["name"] != own["name"]:
                edges[own["name"]].add(other["name"])
    lines = [
        "# Module map. DRAFTED by slopgate from today's folders and imports - run the",
        "# bundled adoption architecture review to confirm owners, public entry files, allowed directions and",
        "# file layout. Every allowed direction below was OBSERVED, not designed: tighten it.",
        "",
        "[map]",
        'python_roots = ["", "src"]',
        "",
        "[map.aliases]",
        '# "@/" = "src/"',
    ]
    for module in draft["module"]:
        quoted = ", ".join(f'"{v}"' for v in sorted(edges[module["name"]]))
        public = ", ".join(f'"{v}"' for v in module["public"])
        lines += [
            "",
            "[[module]]",
            f'name = "{module["name"]}"',
            f'path = "{module["path"]}"',
            'owns = "TODO(owner-review): one line on what this module owns"',
            f"public = [{public}]",
            f"may_depend_on = [{quoted}]",
            "layout = []  # e.g. [\"models/*.py\", \"services.py\", \"views.py\"]; empty = any",
        ]
    return "\n".join(lines) + "\n"


def map_differences(current: dict, target: dict) -> list[str]:
    """List what still separates the enforced map from the target map, module by module.

    The engine never moves code or edits either map: this is a progress read. The owner approves a
    migration step, the code moves, and the enforced map is edited to match that step.
    """
    now = {m["name"]: m for m in current.get("module", [])}
    goal = {m["name"]: m for m in target.get("module", [])}
    gaps = [f"create module {n} at {goal[n]['path']}" for n in sorted(goal.keys() - now.keys())]
    gaps += [f"retire module {n} ({now[n]['path']})" for n in sorted(now.keys() - goal.keys())]
    for name in sorted(now.keys() & goal.keys()):
        here, there = now[name], goal[name]
        if here["path"] != there["path"]:
            gaps.append(f"move module {name}: {here['path']} -> {there['path']}")
        extra = set(here.get("may_depend_on", [])) - set(there.get("may_depend_on", []))
        if extra:
            gaps.append(f"cut dependency {name} -> {sorted(extra)}")
        for key in ("public", "layout"):
            if sorted(here.get(key, [])) != sorted(there.get(key, [])):
                gaps.append(f"{name}.{key}: {here.get(key, [])} -> {there.get(key, [])}")
    return gaps
