---
name: crucible-forge
description: Portable hard-decision tribunal for architecture, strategy, trading-risk, migration, pricing, kill-or-keep, and pre-commit gate decisions. Use when a decision is costly, contested, evidence-sensitive, identity-sensitive, or the user asks to stress-test, run a tribunal, forge a verdict, decide between options, or prove a plan clears a 10x bar.
metadata:
  short-description: Evidence-bound decision tribunal
---

<!-- Public edition: adapted for portable installation and subscription-aware model selection. See collection NOTICE. -->

# Crucible Forge

Use this skill to turn a consequential decision into an auditable verdict. Map claims to
sources, preserve dissent, and refuse to approve when material evidence is missing.

## Runtime compatibility

Read [model and tool selection](references/model-policy.md) before selecting models or delegating.
Use the current host's authorized capabilities. No provider dispatcher or paid API is bundled.

## What it adds

- A decision-time evidence ledger: source, date, uncertainty, and consequence if wrong.
- Point-in-time checks for data decisions: could the input have been known when the decision was made?
- Independent first passes when separate agents are authorized and available.
- The strongest opposing argument before rejection.
- Explicit reversal conditions and preserved dissent.
- Offline JSON validation and conservative aggregation of independently obtained panel responses.

## When To Use

- Architecture or stack tradeoffs.
- Strategy, positioning, pricing, offer, hiring, firing, or kill-or-keep decisions.
- ML/data/trading plans where lookahead, leakage, calibration, or validation discipline matters.
- High-blast-radius code gates, schema migrations, live trading thresholds, launch gates, or money-moving paths.
- Any user wording like "should we", "worth it", "stress test", "run tribunal", "10x", "go/no-go", or "forge verdict".

Do not use for trivial reversible choices, simple lookups, or already-decided implementation unless the implementation carries high irreversible risk.

## Mandatory Intake

1. Restate the decision in one sentence.
2. Classify stakes:
   - `reversible`: cheap to undo, low blast radius.
   - `expensive`: meaningful time, money, customer, architecture, data, or reputation cost.
   - `existential`: live capital, legal/compliance, core strategy, irreversible migration, or company-level bet.
3. Load the project's identity or strategy file (for example `identity.md`) before strategy-level guidance if present. If missing and identity is required, ask the minimum question needed.
4. Load project instructions (`AGENTS.md`, `CLAUDE.md`, `README.md`) only as needed.
5. Define the baseline and the 10x claim. If it is correctness/blocker work, say so instead of pretending it is 10x strategy.

## Evidence Ladder

Score evidence by the strongest available tier:

- `A`: live source, primary docs, reproducible command, dataset row, file line, contract, or current system output.
- `B`: internal docs, recent project history, validated benchmark, or directly relevant prior incident.
- `C`: plausible inference from adjacent evidence.
- `D`: training-data prior or unsupported expert intuition.

Rules:

- A verdict depending on `D` evidence is capped at confidence `0.55`.
- A verdict depending on `C` evidence is capped at confidence `0.70`.
- Missing evidence required to prevent major downside yields `INSUFFICIENT_EVIDENCE`.
- Web or live-doc verification is mandatory for current prices, laws, APIs, model capabilities, product limits, external benchmarks, and time-sensitive research claims.

## Leakage Gate

For trading, ML, analytics, replay, corpus, or data-product decisions, run this check before recommending adoption:

1. Name every inference-time feature, label, threshold, or ranking.
2. For each one, identify `T_decision`.
3. Prove the value was knowable using only information available before `T_decision`.
4. Check late-arrival, backfill, revision, corporate-action, survivorship, dedupe, cluster, and train/test contamination paths.
5. Demand tests for point-in-time invariance, embargo/purge, walk-forward or CPCV, and training/inference parity.

If any critical feature fails this check, verdict is `FAIL` or `INSUFFICIENT_EVIDENCE`.

## Tribunal Modes

### Compact Mode

Use when subagents are not explicitly authorized or the decision can be resolved locally.

1. Scout: gather direct evidence.
2. Red Team: strongest failure case.
3. Steel Man: strongest adoption case.
4. Constraint Keeper: fit against the identity or strategy file, project rules, blast radius, and constraints.
5. Judge: issue verdict with confidence, evidence, objections, kill conditions, and next steps.

### Sealed Multi-Agent Mode

Use only when the user explicitly asks for agents, panelists, delegation, or parallel tribunal work.

1. Dispatch independent subagents with disjoint roles.
2. Do not pass other panelists' drafts into round 1.
3. Reveal after all round-1 outputs are complete.
4. Run cross-examination, inversion for high-stakes splits, then final sealed vote.
5. Synthesize with dissent preserved.

## Output Shapes

Default verdict:

```json
{
  "panelist": "crucible_forge",
  "verdict": "PASS|FAIL|INSUFFICIENT_EVIDENCE",
  "decision": "accept|reject|accept_with_modification|defer",
  "confidence": 0.0,
  "evidence_provided": [],
  "evidence_demanded": [],
  "outstanding_objections": [],
  "kill_conditions": [],
  "next_3_steps": []
}
```

If the user provides a schema, obey the user's schema exactly.

## Fail-Closed Rules

- No unverifiable expected-impact claim.
- No future-data feature without point-in-time proof.
- No strategic recommendation that conflicts with the identity or strategy file unless the conflict is explicit.
- No adoption verdict without kill conditions.
- No consensus theater: if the split is real, return `INSUFFICIENT_EVIDENCE`.
- No implementation hidden inside a verdict unless the user explicitly asked to execute.

## References

- Read `references/panelists.md` when selecting tribunal roles.
- Read `references/checklists.md` for decision-specific gates.
- Use `templates/verdict.schema.json` when the user does not provide a schema.
- Use `scripts/aggregate_verdicts.py` to combine saved panel JSON files offline. This does not call models.
- Treat confidence as an estimate, not a calibrated probability; never invent a Brier score without outcomes.
- Run `scripts/validate_verdict.py <json-file>` before claiming a stored verdict is valid.
