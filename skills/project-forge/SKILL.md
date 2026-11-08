---
name: project-forge
description: Orient and advance a real project using its current files, operating doctrine, stage, blockers, and specialist-routing rules. Use at project entry, after a major handoff or stage change, when the user asks what to do next, or when adopting an existing project into a source-of-truth workflow. Skip isolated questions and mid-task continuations that already have current context.
---

<!-- Public edition: adapted for portable installation and subscription-aware model selection. See collection NOTICE. -->

# Project Forge

Identify the highest-leverage next action from live project evidence, then execute or route within the user's authority.

Do not act as permanent memory. Repository source files are authoritative; memory and generated maps help locate evidence only.

## Runtime compatibility

Read [model and tool selection](references/model-policy.md) before selecting models or delegating.
The host and user control tools, permissions, subscriptions, and costs.

## Preconditions

Require readable project files or user-supplied source material. A verbal project description can support planning, but not a verified project-state audit.

Before non-trivial work:

1. locate and read the nearest `AGENTS.md` or equivalent doctrine;
2. follow its boot order and skill-routing rules;
3. inspect version-control revision and dirty state when relevant;
4. protect user changes and confirm the permitted mutation boundary.

Never impose this skill's generic filenames or lifecycle model over a repository's own doctrine.

## Source priority

Use the project's declared hierarchy. If none exists, prefer:

1. current user instruction;
2. repository operating doctrine;
3. current-state file such as `current-state.md`;
4. identity or strategy file such as `identity.md`;
5. accepted architecture decisions, specifications, contracts, and roadmaps;
6. active progress or handoff records;
7. version-control and runtime evidence;
8. memory or generated maps;
9. labelled inference.

For factual live-state claims, current source and runtime evidence outrank stale recorded state.
When sources disagree, report the conflict and apply the repository's precedence rule. Do not silently reconcile canonical files.

## Operating loop

### 1. Orient

Read only the files needed to establish:

- project identity and stage;
- current revision, worktree state, and protected changes;
- last verified outcome;
- current blocker and intended next action;
- applicable doctrine, architecture, and specialist routing;
- stale generated context or unresolved handoff state.

For large repositories, use graph or index artifacts for navigation only. If a staleness marker exists, read it before relying on generated context and follow the project's graph update rules.

### 2. Audit the claimed state

Verify claims against the real substrate. Choose checks proportionate to the question:

- source and caller inspection for implementation state;
- tests for behaviour covered by those tests;
- hosted checks for remote gate state;
- browser or runtime use for user-visible behaviour;
- database, concurrency, security, money, tenancy, or external-service probes for those exact boundaries.

Do not turn a general project audit into an exhaustive checklist of files the repository never adopted. Treat missing generic artifacts as suggestions unless local doctrine requires them.

### 3. Recommend and route

State one recommended action that addresses the current bottleneck. Explain why it outranks the nearest alternative.

Consult the repository's routing ledger or applicable skill descriptions before acting. Route by task and capability, not by hard-coded provider or model name. Pass specialists the current source files, constraints, evidence boundaries, and expected output.

If the user already gave a clear task, do not pause for a generic orientation ceremony. Orient silently, announce the relevant boundary, and proceed.

Ask only when missing authority or a materially different choice prevents safe progress.

### 4. Execute and close

For implementation, make the smallest useful change that satisfies the live gate and preserves existing architecture. Verify in proportion to risk.

Apply the checks lifecycle below before the first code task. Before calling a task or stage done, or recording any stage transition, require `check` to exit 0 (or the repository's native gates to pass). A failed or unavailable check blocks that claim and transition; report tools marked NOT RUN explicitly, never as passes.

Update project-state, progress, handoff, graph, or memory artifacts only when:

- the user asked for the update;
- repository doctrine requires it for the executed change; or
- it is an ordinary in-scope implementation step and the exact facts are verified.

Do not edit state files for read-only advice, status questions, or aborted work. Do not write persistent memory without explicit user authorization.

Separate:

- changed versus merely inspected;
- locally verified versus independently reviewed;
- committed versus pushed;
- hosted checks versus local checks;
- merged versus open work;
- deployed versus built;
- visibly verified versus inferred.

## Code quality checks

For projects with source code, use [$anti-slop-gates](../anti-slop-gates/SKILL.md) as the checks pack; resolve its engine relative to the installed skill folder:

```bash
# From the collection root; use the installed skill path after installation.
E=skills/anti-slop-gates/scripts/slopgate.py
python3 "$E" init --root <repo>    # new project, before the first code task
python3 "$E" adopt --root <repo>   # existing codebase without the checks
python3 "$E" check --root <repo>   # before completion claims or stage transitions
python3 "$E" update --root <repo>  # lock in lower counts after paying down debt
python3 "$E" target --root <repo>  # show remaining architecture migration steps
```

- Check for native gates **before setup**. Repositories with existing quality checks keep their own gates. A `.native-quality-gates` marker makes that choice mechanically explicit. The pack refuses them (exit 2); never bypass that refusal or flag missing `.slopgates/` as a gap.
- If `.slopgates/` already exists, follow the pack's existing-state procedure, including its one-time code-pack upgrade for older setups. Do not re-baseline. Python and Node/TypeScript adapters use project tools; per-file limits only go down, and new files have zero debt allowance. Never raise limits, delete configuration, or skip a failure to advance.
- `adopt` requires both judgment passes: read-only code review using the bundled Anti-Slop adoption checklist adds ranked findings to `.slopgates/CLEANUP-PLAN.md`; architecture review confirms `.slopgates/modules.toml` and writes `.slopgates/modules.target.toml` plus `.slopgates/TARGET-MAP.md`. The owner approves each migration step before implementation; the pack never restructures code itself.
- Before building, read [the pack's rules](../anti-slop-gates/references/RULES.md) and `.slopgates/modules.toml`. Pass these paths and the check command to every building agent; include them when authorized to create or refresh project `AGENTS.md`. Web work starts with the pack's `vocab` command.
- Before creating or moving a module, or changing an import boundary, perform architecture review using that checklist; update `.slopgates/modules.toml` with the approved change. Use `target` to track migration toward the target map.
- Wire `check` into pre-commit and `check --base origin/main` into CI, following repository conventions and the pack's `--vendor` option when CI needs a local engine.

For native quality gates, use the existing architecture and boundary documents in place of
`.slopgates/` files. Keep the architecture review and owner approval requirements.

If additional specialist skills are unavailable, use [the bundled adoption review](../anti-slop-gates/references/adoption-review.md).
The installer includes Anti-Slop Gates with Project Forge. Resolve these sibling references from
the installation directory. Native project checks remain authoritative.

## Adoption mode

Use only when no current-state workflow exists and the user asks to establish one.

Checks adoption is independent of this mode: an existing `current-state.md` does not exempt a codebase from the checks lifecycle above.

1. inventory real files, stack, entry points, version-control state, and existing conventions;
2. recover identity, current stage, constraints, and next action without inventing them;
3. propose the minimum source-of-truth artifacts and ownership rules;
4. obtain direction before creating doctrine that changes how the project operates;
5. generate from verified files and label unknowns.

Missing `current-state.md`, `identity.md`, or `PROGRESS.md` does not by itself authorize creating them.

## Output

Lead with:

```text
PROJECT: <name>    STAGE: <verified stage or unknown>
CURRENT: <last verified outcome and active blocker>
RECOMMENDED: <one highest-leverage next action>
EVIDENCE: <key files, revision, checks, or external state>
BOUNDARIES: <important unknowns or authority limits>
```

For completed project work, follow the repository's required handoff format. Otherwise state what changed, how it was verified, and what remains.

## Hard rules

- Never invent project state, stage, architecture, approval, or readiness.
- Never trust aspirational documents over current-state evidence.
- Never overwrite or normalize a dirty worktree without explicit authority.
- Never regenerate doctrine, graphs, or progress files merely because they exist.
- Never call local green checks merge-ready, deployed, or user-verified.
- Never use a specialist skill as proof; reopen the source and verify its claims.
