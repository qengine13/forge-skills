---
name: plan-hygiene
description: Run a pre-flight before substantive plans, scope or doctrine changes, new or revised skills and hooks, architecture-bearing work, or tasks likely to exceed about 30 minutes. Verify the goal, existing substrate, statistical viability where relevant, project doctrine, prior decisions, scope boundaries, and measurable completion gates before implementation. Usually skip routine questions, tiny edits, and narrow bug fixes.
---

<!-- Public edition: adapted for portable installation and subscription-aware model selection. See collection NOTICE. -->

# Plan Hygiene

Make important work testable before it starts. Surface missing foundations, doctrine conflicts, unmeasurable promises, and unauthorized scope while correction is still cheap.

The user's current instruction and repository doctrine control this workflow. Do not turn a plan-only request into implementation, mutate protected files, or invent approval.

## Runtime compatibility

Read [model and tool selection](references/model-policy.md) before selecting models or delegating.
The host and user control tools, permissions, subscriptions, and costs.

## Inputs

Collect:

- the user's exact objective and boundaries;
- current project doctrine and source-of-truth files;
- the proposed plan or the facts needed to draft it;
- relevant code, data, commands, external state, and prior decisions;
- the intended completion claim.

Use evidence from current files, command results, tests, databases, authoritative external sources, or explicit user facts. Memory is a recall layer, not proof.

## Verdicts

- `PROCEED` — the plan is sufficiently grounded and completion is testable;
- `RE_SCOPE` — the goal is viable only after narrowing or sequencing;
- `PRECURSOR_FIRST` — a missing foundation must be built or verified first;
- `AMEND_FIRST` — canonical doctrine or an accepted decision must change first;
- `KILL` — the proposed work cannot produce its promised outcome under current conditions.

Do not proceed past any verdict except `PROCEED` without explicit user direction resolving the reason.

## Seven-gate pre-flight

### Gate 0 — Trigger

State `FIRES` or `DOES_NOT_FIRE`, the reason, and a rough effort estimate. If the skill does not fire, stop the protocol and continue with the task.

### Gate 1 — Goal and contract

Write:

- one action-oriented goal;
- audience or executor;
- hard must-do and must-not-do constraints;
- observable success criteria;
- at least two plausible failure modes;
- estimated agent effort and calendar span.

Ask only when a missing answer would materially change the result and cannot be recovered from current sources.

Do not turn a missing dependency into a prescribed architecture. Separate required behaviours
from optional implementation choices; do not require a queue, tenancy model, or storage product
without evidence that the actual task needs it.

### Gate 2 — Substrate

Verify every material assumption about existing code, files, data, routes, commands, schemas, hooks, infrastructure, or external state.

Search broadly, then read the relevant source. Trace entry points, callers, configuration, tests, and ownership boundaries where the plan depends on them. For dependency failures, inspect installation integrity before proposing version changes.

Record:

| Assumption | Exists? | Evidence | Impact if absent |
|---|---|---|---|

Use `PRECURSOR_FIRST`, `RE_SCOPE`, or `KILL` when an absent substrate changes cost, risk, or feasibility materially. Mark this gate not applicable only for genuine doctrine-only or strategy-only work.

### Gate 3 — Measurement and statistical viability

Run when the plan depends on experiments, cohorts, backtests, inference, replay validity, measurable effects, or performance claims.

State the sample or dataset, effect or threshold, instrumentation, comparison method, and whether the available evidence can support the intended claim. Include alpha, correction, power, or minimum detectable effect when those concepts apply.

Use `RE_SCOPE` or `KILL` when the promised conclusion is not measurable within the available data or timeline. Mark deterministic implementation and non-evaluative documentation work not applicable.

### Gate 4 — Doctrine, identity, and architecture

Read the governing files directly. Check business identity, current project state, architecture decisions, security and compliance rules, release policy, skill routing, and edit boundaries that apply.

Record:

| Requirement | Plan treatment | Evidence or gap |
|---|---|---|

Use `AMEND_FIRST` when the plan conflicts with canonical doctrine and no separately attributable user decision changes it. Invoke any mandatory specialist workflow named by the repository before proceeding.

### Gate 5 — Prior decisions and failed attempts

Run the environment's approved memory quick pass plus targeted repository-history search when prior context is likely relevant. Reopen current files before relying on any recalled claim.

Record only context that changes the plan, the source used, and any unavailable evidence. A prior kill verdict remains controlling unless current evidence shows that its blocking condition changed.

Do not write persistent memory unless the user explicitly authorizes it.

### Gate 6 — Challenge

For work likely to exceed two hours or alter doctrine or architecture, challenge the plan against Gates 1–5 and its predicted failure modes.

- Use independent review when the user or project requires it.
- Use a bounded subagent critique only when delegation is authorized.
- Otherwise perform and label a `SELF_CHECK`.

Return `PASS`, `SCOPE_CHANGE`, `KILL`, `SELF_CHECK`, or `SKIP_WITH_REASON`, with evidence. Review findings are inputs; accept only those supported by the current task and files.

## Artifact and completion gates

For substantive work, write the repository's prescribed pre-flight artifact. If no convention exists, use `docs/pre-flight/YYYY-MM-DD-<scope>.md` and `references/verdict-template.md`.

Every `PROCEED` artifact must include non-empty completion gates covering:

- core promised behaviours;
- hard dependencies and risk boundaries;
- relevant external or user-visible surfaces;
- exact verification evidence;
- local, remote, review, merge, deployment, and runtime states when applicable.

If meaningful completion gates cannot be written, the plan is not ready.

## Final check

Before proceeding, confirm:

- every pass has evidence;
- every skip has a reason;
- the smallest useful scope was selected;
- explicit edit, approval, deployment, and communication boundaries are preserved;
- the plan does not treat memory, generated maps, local green checks, or agent prose as stronger evidence than they are;
- required user ratification is separately attributable and recorded.

Return the verdict first, then the goal, material gate findings, completion gates, estimate, and any decision the user must make.
