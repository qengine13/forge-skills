---
name: completion-hygiene
description: Run before saying done, complete, ready, merge-ready, shipped, or milestone reached for a feature, phase, sprint, release, or project. Audit the plan against real artifacts, production invocation paths, and verification evidence; emit a literal completion verdict and supported wording, then narrow or block unsupported claims. Do not use for routine status reports, exploratory work, or small fixes with no completion claim.
---

<!-- Public edition: adapted for portable installation and subscription-aware model selection. See collection NOTICE. -->

# Completion Hygiene

Prevent paper-tiger completion claims: code or documentation that exists but is not reachable, exercised, or proven through the promised real path.

Repository doctrine overrides this generic workflow. Treat passing tests, local commits, remote publication, independent review, hosted checks, deployment, and user-visible verification as separate claims unless the project explicitly combines them.

## Runtime compatibility

Read [model and tool selection](references/model-policy.md) before selecting models or delegating.
The host and user control tools, permissions, subscriptions, and costs.

## Mandatory result on every firing

Even when the user asks only for announcement, commit, or handoff wording, emit these fields before
any explanatory prose:

```text
VERDICT: CLAIM_SUPPORTED / CLAIM_NARROWED / CLAIM_BLOCKED / PLAN_AMENDMENT_REQUIRED
SUPPORTED WORDING: <exact wording the current evidence permits, or NONE>

EVIDENCE BOUNDARIES:
- Local tree: <checked and result / not checked>
- Remote state: <checked and result / not checked>
- Hosted checks: <checked and result / not checked>
- Runtime/browser: <checked and result / not checked>

PROMISE MATRIX:
| Promise | Source | Artifact evidence | Invocation evidence | Status |
```

Do not collapse the audit into safe-sounding prose. Repository reporting doctrine may require an
additional status prefix, but it does not remove this literal verdict contract.

Matrix status must be exactly `PROVEN`, `EXISTS_UNPROVEN`, `MISSING`, or `NOT_APPLICABLE`.
User-supplied artifact existence without proven production reachability is `EXISTS_UNPROVEN`, never
`PROVEN`. Use `MISSING` only after inspection affirmatively establishes that the promised artifact
or path does not exist. Use `EXISTS_UNPROVEN` when evidence is absent, unavailable, or not checked.

## Inputs

Collect before auditing:

- the exact completion claim;
- the originating plan, pre-flight, specification, or recovered promise list;
- current repository doctrine and source-of-truth files;
- the live diff and relevant source files;
- available test, review, continuous-integration, deployment, and runtime evidence.

If the work had no written plan, recover the promises from the user's request and authoritative project files. Do not invent a broader contract or retroactively label unplanned work as a completed phase.

## Four-gate protocol

Run the gates in order. Keep the evidence concise, but do not compress missing proof into a conversational assurance.

### Gate A — Recover the promises

Read the governing plan or recover a bounded promise list from the current request and canonical files. Extract every promised:

- artifact;
- behaviour and trigger;
- integration or invocation path;
- acceptance or completion gate;
- approval, review, publication, or deployment boundary.

Cite each promise to its source. If no defensible promise list can be recovered, stop the broad completion claim and report what narrower statement is supported.

### Gate B — Inventory the artifacts

Verify each promised artifact in the real files. Record the path and relevant line or exact external evidence. An enum, schema, module, test double, comment, or document is existence evidence only.

If a required artifact is missing, either implement it within the authorized scope or narrow the claim. Do not continue toward a green verdict on the original claim.

### Gate C — Prove invocation and behaviour

For each behavioural promise, trace a real path from the user, command, request, scheduled job, hook, or other production entry point to the implementation.

Classify each promise:

- `PROVEN` — artifact exists, a real path reaches it, and appropriate behaviour evidence passes;
- `EXISTS_UNPROVEN` — artifact exists but reachability or behaviour is not proven;
- `MISSING` — promised artifact or path does not exist;
- `NOT_APPLICABLE` — the plan explicitly makes invocation unnecessary, with the reason recorded.

Tests support the path but do not replace it. A unit test that calls a function directly does not prove production wiring. For user-interface claims, use visible browser evidence when available. For concurrency, tenancy, money, security, migration, or external-system claims, require evidence that exercises that exact boundary.

Use a second reviewer or subagent when the project requires independent review or the change is large enough to benefit from it. Route by capability and pass the raw promise list and files, not a suggested diagnosis. A self-check is acceptable only when project doctrine permits it; label it.

Any core `EXISTS_UNPROVEN` or `MISSING` promise blocks the original completion claim.

### Gate D — Write the verdict

Write the repository's required completion artifact. If no local convention exists, use `docs/completion-audit/YYYY-MM-DD-<scope>.md` and the template in `references/audit-template.md`.

Choose exactly one verdict:

- `CLAIM_SUPPORTED` — every promise required by the claim is proven;
- `CLAIM_NARROWED` — evidence supports any meaningful smaller claim, which must be rewritten exactly;
- `CLAIM_BLOCKED` — no meaningful claim in the requested family is currently supported;
- `PLAN_AMENDMENT_REQUIRED` — the plan must change before the claim can be made.

When test or release evidence is unavailable, say "not verified" or "no result supplied";
do not assert the tests never ran or the release never happened.

Record absent evidence as absent. Never convert “not checked,” “cannot access,” or “would probably pass” into a pass.

## Hard rules

- Verify the live tree and current external state; memory and prior summaries are retrieval aids only.
- Distinguish built, locally tested, independently reviewed, committed, pushed, continuous-integration green, merged, deployed, and visibly verified.
- Do not bypass hooks or rename a claim to evade project doctrine.
- Re-run the affected gates after remediation or scope changes.
- Do not write persistent memory unless the user explicitly authorizes it. If authorized, capture only the verified verdict and durable lesson after the artifact exists.
- Founder or owner approval requires separately attributable evidence. Agent prose is not ratification.
- If the task only asks for a status report, audit the evidence needed for that report without mutating files or external state.

## Output

Return:

1. the literal `VERDICT` and `SUPPORTED WORDING` fields;
2. the promise matrix with file, line, command, and external-state evidence as applicable;
3. the verification commands and results;
4. remaining risks or blockers;
5. the completion-audit path when an artifact was required.

Use `references/origin-paper-tigers.md` when training or explaining why existence is not invocation. It is background evidence, not a substitute for the current audit.
