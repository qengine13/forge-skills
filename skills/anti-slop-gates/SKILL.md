---
name: anti-slop-gates
description: >-
  Mandatory before claiming any code done, for supported Python, Node/TypeScript, web templates
  and stylesheets. Deterministic stdlib-only gates that make "don't write slop" an exit code:
  format, lint, complexity budget, security, type errors, silenced warnings without a reason,
  docstrings with an example, duplicate blocks, prior-art receipts, module map and import
  boundaries, shared helpers, plus the web DOM/naming/ratchet gates. Every count only shrinks.
  `adopt` retrofits an existing codebase. Refuses repositories with the native gate markers listed below.
  Triggers: any code build/edit, "slopgate", "anti-slop", "adopt the checks", "code quality gates".
---

<!-- Public edition: adapted for portable installation and subscription-aware model selection. See collection NOTICE. -->

# Anti-Slop Gates

Inspect whether `.slopgates/` and the native-gate marker files below exist before setup.

**Read [references/RULES.md](references/RULES.md) before writing code in a project that carries
`.slopgates/`.** It is the doctrine the gates enforce: search before building, do less, code lives
where the module map says, plain docstrings with an example, never silence a check without a
reason, proof before claims, break your own fix on purpose.

**The engine:** `scripts/slopgate.py` (Python 3.11+, zero dependencies). Language adapters shell
out to each language's own tools, found in the project (`.venv/bin`, `node_modules/.bin`) or on
PATH — ruff and mypy for Python; prettier, eslint and tsc for Node/TypeScript. Nothing is
downloaded. **Per-project state:** a committed `.slopgates/` directory. The engine stays HERE, so
updates reach every project; `--vendor` copies it into `tools/slopgates/` for CI runners.

## Runtime compatibility

Read [model and tool selection](references/model-policy.md) before selecting models or delegating.
The host and user control tools, permissions, subscriptions, and costs.

## Precedence — read first

1. **A project with native gates keeps them.** The engine
   refuses mechanically (exit 2) when the repository carries a `.native-quality-gates`
   marker. Existing checks take precedence even without that marker. Never double-gate.
2. Project doctrine (CLAUDE.md / AGENTS.md) outranks this skill's defaults. Budgets and limits are
   edited in `.slopgates/config.toml`, deliberately.

## Starting — new project or existing code

```bash
# From the collection root; when installed, resolve this script inside the skill folder.
E=skills/anti-slop-gates/scripts/slopgate.py
python3 "$E" init  --root <repo>   # new project: freezes today's (near-zero) state
python3 "$E" adopt --root <repo>   # existing codebase: same, plus a ranked clean-up plan
```

Both write `config.toml` (tools found, budgets), `code_baselines.json` (today's per-file counts),
`modules.toml` (the as-is module map, drafted from today's folders and imports) and the web gates'
files. **Today's count is each file's limit: nothing already there blocks day one, nothing can get
worse, and a file not yet in the baselines is held at zero.** A tool that is missing at set-up is
printed as NOT RUN and listed in the config; a tool enabled at set-up that later cannot run FAILS
the gate. Init never reruns; on a project initialised before the code pack existed, it adds the
code pack only.

**`adopt` then needs two judgment passes from you (the engine cannot run a skill):**

1. Perform the read-only code review in [the adoption checklist](references/adoption-review.md):
   static analysis, duplicate and dead code, circular imports, and dependency health. Add ranked
   findings to `.slopgates/CLEANUP-PLAN.md`. Include security review for critical paths.
2. Perform the checklist's architecture review: confirm the
   as-is map in `modules.toml` (owners, public entry files, allowed directions — the draft allowed
   every direction it OBSERVED; tighten what is wrong only as a recorded limit), then write the
   TARGET map `modules.target.toml` and `TARGET-MAP.md` (each move with its reason; a migration
   path in small, separately shippable steps ranked by gain against risk). **The pack never
   restructures by itself.** The owner approves each step; when its code lands, edit
   `modules.toml` to match that step. `slopgate.py target` prints what remains.

For HTML-only repositories with no importable modules, record the architecture pass as not
applicable in the cleanup plan; no target map or migration plan is required.

## The loop — every build or edit

1. Read `references/RULES.md` and `.slopgates/modules.toml`. Web work: `slopgate.py vocab` first.
2. Build. Creating or moving a module, or changing a boundary: perform the architecture review first.
3. Before claiming done: `slopgate.py check --root <repo>`. **Exit 1 = you are not done.** Fix the
   finding — never re-baseline, never delete the config, never "temporarily" skip.
4. Paid down old debt: `slopgate.py update` locks the lower count in (it refuses to raise any).

**Wiring (once per project):** `python3 <engine> check` in pre-commit, and
`python3 <engine> check --base origin/main` in CI.

## What the code gates catch (supported adapters)

Each is a per-file count against `code_baselines.json`; a moved file keeps its debt only if its
baseline entry moves with it.

| Check | Catches | Source |
|---|---|---|
| format | a file the formatter would rewrite (ruff format / prettier) | adapter |
| lint | the project's own lint findings (ruff / eslint) | adapter |
| complexity | a function over 10 branches, 50 statements or 5 parameters (C901/PLR0915/PLR0913; eslint complexity/max-statements/max-params) | adapter |
| security | ruff's bandit family (S), unless the line carries `# security-reviewed: <reason>` | adapter |
| types | type errors per file (mypy / tsc); counts must add up to the checker's own total, a crash fails | adapter |
| silenced | `noqa`, `type: ignore`, `eslint-disable`, `@ts-ignore`... with no written reason (` -- reason` or `reason: ...`) | engine |
| docstring | a public Python def/class without a purpose line and `Example:`; an exported TS/JS function/class without TSDoc purpose and `@example` | engine |
| duplicate | a block of 6+ meaningful lines that exists elsewhere (stdlib finder, no network) | engine |
| prior-art | `Verdict: REUSE/PORT/EXTEND` without a live `path:line`; `Verdict: BUILD` without a probe | engine |
| boundary | an import against the module map: a forbidden direction, or a reach-in past a module's public entry | engine |
| layout | a grab-bag file (`utils.py`, `helpers.ts`...), a file outside `source_roots`, or outside its module's declared layout | engine |
| helper | a new copy of a concern the `[[helper]]` register gives one canonical home | engine |
| manifest | opt-in (`[code.change] require_manifest`): a change against `--base` carries exactly one manifest file | engine |

Adding a language: write a `run_<tool>` in `scripts/adapters.py` that returns per-file counts and
register it in `TOOLS`; it rides the same ratchet.

## What the web gates catch

**dom** — structure debt, baseline-ratcheted per file:
DOM depth over budget (default 10), chains of 3+ single-child div/span wrappers, inline `style=`,
`<style>` blocks, inline `<script>` blocks, utility-class soup in semantic templates (auto-off on
Tailwind projects), interactive-inside-interactive, hardcoded layout px in CSS, selector chains
over 4 parts / 5 class hooks, `!important`. New files are born clean (no baseline = zero allowed).

**naming** — the vocabulary wall (the anti-"AI names" gate):
Every literal class emitted (`class=` and `className=`) must be project vocabulary or a declared
`[additions]` entry with a reason. An invented near-duplicate gets told the EXISTING name to use
(`.tile-cell` → "this project already names this: `.cell`").
Machine-name tics fail on sight for NEW names: stacked filler nouns (`content-wrapper-inner`),
numbered generics (`box2`), disposable suffixes (`-v2`, `-new`, `-final`), puffery prefixes
(`enhanced-`), 5+ hyphen segments. There is deliberately NO regenerate mode — adding a name costs
one manifest line with a reason, and that cost is the mechanism.

**ratchet** — tree-wide counters that only shrink:
inline event handlers (`onclick=`), `&nbsp;` runs as layout, `<br><br>` stacking, `z-index: 999+`.
Projects add their own `[[ratchet]]` tables (name + regex + remedy) in `config.toml`.

## Escape hatch

A deliberate exception is written next to the line, in the open:
`<!-- slopgate-allow: html-inline-style email templates need inline CSS -->`
A bare `slopgate-allow` without category + reason is itself a failure — the marker cannot be
cargo-culted.

## Honest boundaries

- JSX/TSX/Vue/Svelte/Astro: covered by the NAMING gate out of the box (`naming_globs` scans
  `className`/`class` string literals, including `className={"..."}`); the DOM gate parses real
  HTML/server templates only (html.parser cannot parse JSX). React projects get naming +
  ratchets; add JSX-suffix ratchets in config for inline `style={{`.
- CSS selector complexity is judged line-locally; a selector wrapped across lines by a formatter
  can slip under the budget. This is a limitation of line-based scanning.
- The gates catch STRUCTURE and NAMING slop mechanically. They do not judge visual design —
  that is design-system / design-taste territory, a different skill.
- Grandfathered debt is visible (`report`, notes) but only blocks when it GROWS.

## Portable review dependencies

Use [the bundled adoption review checklist](references/adoption-review.md) for code and
architecture review. Additional specialist skills are optional. Do not skip review or change
the module map without the owner's authority.

## Provenance

See the collection's `NOTICE` for author and license attribution, and the provenance guide
for the public edition's scope. Attribution does not establish compatibility test coverage.
