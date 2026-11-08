# The rules — read before writing any code in a project that carries `.slopgates/`

These are the habits the checks enforce. The checks catch the habit after the fact; reading this
first means they never have to. Every rule here is either enforced by `slopgate.py check` (marked
**[checked]**) or carried by you (marked **[yours]**).

Use the project's own language conventions. Keep functions focused, explain public interfaces,
and preserve ownership boundaries. No private machine-local rule file is required.

## 1. Search before you build [yours + checked]

"It doesn't exist yet" is a claim, and the most expensive one to get wrong. Before building,
search the tree in **the owner's words, not your design's words** — the thing you would rebuild is
usually named differently. Look in every top-level folder, not the two you'd guess, and in open
pull requests and branches, because the tree only shows what has merged.

Write the verdict down where the work is planned, with its receipt:

- `Verdict: REUSE src/billing/invoice.py:42` — reuse, port and extend must cite the prior art as
  `path:line`, and **that line must still exist**. [checked: `prior-art`]
- `Verdict: BUILD — probed 2026-10-16: \`rg -n "refund|credit note" src\` finds nothing` — a build
  claims absence, so it carries the probe that proved it. [checked: `prior-art`]

## 2. Do less [checked: `complexity`, `duplicate`, `helper`]

The smallest change that solves the task. No abstraction without a real, named second caller. No
error handling for a case that cannot happen. No cleanup riding along with a fix. No solving a more
general problem than the one asked. If it is 50 lines where 15 would do, it is wrong.

- Each function stays inside the budget: 10 branches, 50 statements, 5 parameters. A tripped
  budget means **simplify**, never raise the limit.
- A block of six or more lines that already exists elsewhere is a duplicate — call the original.
- A concern listed in the shared-helper register (`[[helper]]` in `.slopgates/config.toml`) has one
  home. Use it. Repeating it is refused.

## 3. Code lives where the map says [checked: `boundary`, `layout`]

`.slopgates/modules.toml` is the module map: which folder owns which area, which files are its
public entry, which modules it may import, and what file layout it uses.

- Import another module only through its public entry, and only in an allowed direction. Reaching
  into another module's internals is refused.
- No grab-bag files (`utils.py`, `helpers.ts`, ...). Give each concern its own well-named file.
- **Before creating or moving a module, or changing a boundary: perform the architecture review in [adoption-review.md](adoption-review.md), using a
  specialist skill if available.** It owns the map. Change the map deliberately, in the same change, with the reason.
- The map is never restructured by a tool. A target map, where one exists, is reached one
  approved step at a time (see `.slopgates/TARGET-MAP.md`).

## 4. Plain docstrings with an example [checked: `docstring`]

Every public function, method and class says, in plain words, **what it is for**, and shows **how
it is used**:

- Python: a Google docstring — a purpose line, then an `Example:` section (or `>>>` lines).
- TypeScript / JavaScript: a TSDoc block above the export — a purpose line, then `@example`.

Full words, no abbreviations, no jargon shorthand. Full type annotations.

## 5. Never silence a check without saying why [checked: `silenced`, `security`]

`# noqa`, `# type: ignore`, `eslint-disable`, `@ts-ignore` and their kin tell a checker to look
away. Each one carries a written reason on the same line (` -- <reason>` after the directive, or a
second comment `# reason: <text>` where the tool forbids trailing text, as mypy does):

    value = legacy()  # type: ignore[no-any-return]  # reason: vendor stub returns Any until v3
    import plugins  # noqa: F401 -- re-exported for the plugin loader
    // eslint-disable-next-line no-console -- the CLI's only output channel
    // @ts-expect-error the generated client mistypes this field (issue 214)

A security finding that was reviewed and is safe carries `# security-reviewed: <why it is safe>`
on the line the scanner names. An empty reason excuses nothing.

## 6. Proof before claims [yours]

"Done" means the checks pass (`slopgate.py check` exits 0), the tests pass, and you looked at the
real thing working. Report check output honestly — findings are the deliverable. A tool that did
not run is reported as NOT RUN, never as clean.

## 7. Break your own fix on purpose [yours]

A test written beside its code inherits its author's mistakes. After the fix passes, undo the fix
(or plant the bug it guards against) and watch the test fail for the right reason. A test that
still passes with the fix removed proves nothing. Then restore the fix.

## The loop

1. Read this file and `.slopgates/modules.toml`.
2. Search before building (rule 1). Review architecture if a module or boundary moves.
3. Build the smallest thing (rule 2), in the right place (rule 3), documented (rule 4).
4. `python3 <installed-skill>/scripts/slopgate.py check --root <project>` — exit 1 means not done.
5. Cleaned up old debt? `slopgate.py update` locks the lower count in for good.
