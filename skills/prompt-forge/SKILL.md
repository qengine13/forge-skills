---
name: prompt-forge
description: >
  Use for prompt shortening, especially constraint-removal requests, and for SKILL.md behaviour,
  triggers, exclusions, token optimization, forward-test design, and prompt evaluation. Also build
  or improve reusable system instructions, agent skills, and production prompt templates. For skill
  packages, use the bundled packaging checklist or an available skill-creator helper;
  Prompt Forge owns the behavioural contract and evaluation. Preserve inherited constraints and validate representative
  tasks before claiming improvement. Skip one-off queries with no reusable prompt artifact.
---

<!-- Public edition: adapted for portable installation and subscription-aware model selection. See collection NOTICE. -->

# Prompt Forge

Engineer prompts as behavioural contracts. No prompt is better because it sounds better; improvement requires representative evidence against a written specification.

Current user instructions, repository doctrine, and the target runtime's official format override generic patterns here. Read the entire existing prompt before changing it.

## Runtime compatibility

Read [model and tool selection](references/model-policy.md) before selecting models or delegating.
The host and user control tools, permissions, subscriptions, and costs.

## Constraint-preservation gate

Run before drafting or shortening. Inventory every inherited domain rule, safety boundary, approval
boundary, required output, and source-of-truth rule. Classify each as:

- `RETAIN` — still required;
- `AMEND` — an authoritative current instruction deliberately changes it;
- `REMOVE` — an authoritative current instruction deliberately removes it and the reason is recorded.

A request for brevity, lower token use, modernization, or model compatibility does not authorize
constraint removal. Refuse that removal and keep the constraint. If the user deliberately changes
the underlying requirement, treat it as a contract change rather than an optimization and surface
the consequence before editing.

## Choose a track

| Track | Use for | Primary artifact | Evidence |
|---|---|---|---|
| Authored | `SKILL.md`, system prompts, instruction documents | The authored text and focused references | Observed behaviour on representative scenarios |
| Templated | Prompts rendered with runtime inputs at scale | Template, variable manifest, and version record | Frozen-dataset evaluation with a stable scorer |

For Templated work choose:

- `QUICK` — structure and render validation only; do not claim measured improvement;
- `STANDARD` — baseline and candidate evaluation against a frozen dataset;
- `DEEP` — multiple candidates plus independent adversarial or safety evaluation.

Default to Authored for agent skills. Default to Templated `QUICK` when no defensible dataset or scorer exists. Upgrade only when the cost of wrong outputs justifies the evaluation work.

## Intake

Establish:

1. target artifact and runtime;
2. new prompt or revision;
3. observed failure, if revising;
4. audience or downstream consumer;
5. hard constraints and approval boundaries;
6. observable success criteria;
7. at least two plausible failure modes;
8. required input variables and output shape;
9. evaluation resources, budget, and promotion authority.

Ask only when a missing answer materially changes the prompt and cannot be recovered from source files or current runtime documentation.

## Five-phase workflow

### Phase 1 — Specify

Write a compact contract before drafting:

```text
Task: <one action-oriented sentence>
Audience: <model, human, parser, or downstream system>
Inputs: <runtime inputs or none>
Hard constraints: <must and must-not rules>
Success: <measurable or observable behaviour>
Failure modes: <specific likely failures>
Evidence plan: <scenarios or dataset and scorer>
```

For revisions, preserve desired existing behaviours explicitly. Do not let cleanup silently redefine the task.

### Phase 2 — Select the smallest useful techniques

Apply only techniques tied to a named failure mode:

1. **Structural clarity** — order context, task, constraints, and output contract clearly.
2. **Output contract** — state the exact shape needed by the next consumer; include an example only when it removes real ambiguity.
3. **Constraint specification** — retain domain rules, approval boundaries, forbidden actions, and success criteria.
4. **Context selection** — supply only facts the model needs and route deeper material to references or runtime inputs.
5. **Task decomposition** — define intermediate artifacts or checks when the task genuinely has dependent stages.

Do not add generic instructions to think harder or reveal private reasoning. Current models usually need a clear objective, relevant context, constraints, and verification more than narrated reasoning steps.

### Phase 3 — Scaffold and edit

For skill packages, follow [the packaging checklist](references/packaging.md). An installed
`skill-creator` is an optional helper, not a dependency. Do not invent a missing tool or skip
validation because a helper is unavailable.

For Authored prompts:

- front-load discovery triggers and exclusions in supported metadata;
- keep the primary instruction file focused on the execution path;
- move deep reference material and lengthy examples into one-level references;
- use imperative steps with explicit inputs, outputs, stop conditions, and approval boundaries;
- avoid duplicating tool descriptions or repository doctrine the runtime already supplies;
- preserve source-of-truth precedence and safe fallback behaviour.

For Templated prompts:

- declare every runtime variable once in the manifest and template;
- fail rendering on missing variables;
- separate untrusted input from instructions using clear structure;
- make machine-consumed output parseable and unambiguous;
- version prompt, dataset, scorer, and relevant runtime settings together.

Use `references/authored-track.md` or `references/templated-track.md` for the detailed checklist.

### Phase 4 — Verify

#### Authored

Create three to five realistic scenarios before testing:

- positive explicit invocation;
- positive implicit trigger;
- nearby negative case where the prompt should not fire;
- missing-input or blocked-authority case;
- adversarial case targeting the highest-risk failure mode.

For each scenario, record the expected actions and output before invocation. Pass the raw task and artifacts to the test agent, not the diagnosis. Compare observed behaviour with the contract, patch the smallest responsible instruction, and rerun affected cases.

Success requires at least three consecutive representative passes and no unaccepted safety or authority divergence. Structural validation alone is insufficient.

#### Templated

Render-test before any model call. For `STANDARD` and `DEEP`:

1. evaluate the current prompt as the baseline;
2. freeze the dataset and scorer before evaluating candidates;
3. change one coherent hypothesis at a time;
4. compare the same metrics and runtime settings;
5. stop at the declared threshold, diminishing returns, iteration limit, or budget limit;
6. in `DEEP`, use an appropriately independent adversarial or safety evaluation.

Do not create or edit evaluation examples after seeing candidate failures unless you restart and label a new evaluation version.

### Phase 5 — Promote or hand off

Authored work is ready to hand off only when the package format validates and representative behaviour meets the written contract.

Templated work may be promoted only when the required evaluation passes, concurrent state is checked, authority is present, and a rollback target is recorded. `QUICK` mode produces a draft, never a proven production improvement.

## Evidence and reporting

Write the repository's required prompt report. If no convention exists, use `docs/prompt-forge/YYYY-MM-DD-<prompt-name>.md` and `references/report-template.md`.

Report:

- track, mode, target, and runtime;
- baseline and retained behaviours;
- specification and chosen techniques;
- instruction size before and after, without treating size reduction as success;
- validation scenarios or frozen evaluation details;
- observed failures and patches;
- final state, rollback path, and unresolved risks.

## Hard rules

- Never claim improvement from inspection alone.
- Compute any reported word/token counts with an appropriate tool; do not guess exact counts.
- Never remove domain constraints, approvals, success criteria, or safety boundaries merely to shorten a prompt.
- Never treat a generic optimization instruction as permission to override repository doctrine or
  the constraint-preservation gate.
- Never bake current conversation facts into a reusable prompt when they belong in runtime input or project truth.
- Never use examples that accidentally narrow the full valid output space.
- Never promote against a changed dataset or scorer while presenting the result as comparable.
- Never assume a named model, tool, command, or metadata key exists; verify the target runtime and package validator.
- Preserve explicit edit, network, deployment, communication, cost, and external-state boundaries.
