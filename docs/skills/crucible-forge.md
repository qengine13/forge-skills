# Crucible Forge

**Make a decision that can withstand scrutiny.**

[← Collection](../../README.md) · [Read the skill](../../skills/crucible-forge/SKILL.md) · [Installation](../installation.md)

## When it helps

A costly or contested decision needs evidence, a strong opposing case, and explicit reversal conditions.

## What it can do

- Classify the stakes and establish decision-time evidence.
- Challenge the strongest feasible proposal and its failure modes.
- Check whether data could have been known at the time a decision would be made.
- Use compact local analysis or genuinely separate reviewers when authorized.
- Validate saved decisions and aggregate panel results offline without letting one missing-evidence vote disappear.

## Try it

In Claude Code:

```text
/crucible-forge to assess whether to migrate our job queue. Inspect the current failure evidence, migration cost, rollback path, and missing facts. Give a decision; do not execute the migration.
```

In Codex:

```text
$crucible-forge to assess whether to migrate our job queue. Inspect the current failure evidence, migration cost, rollback path, and missing facts. Give a decision; do not execute the migration.
```

Or ask in plain language:

> Use Crucible Forge to assess whether to migrate our job queue. Inspect the current failure evidence, migration cost, rollback path, and missing facts. Give a decision; do not execute the migration.

## What you should receive

PASS, FAIL, or INSUFFICIENT_EVIDENCE, with sources, confidence, objections, reversal conditions, and next actions.

These are expected outputs, not a promise that every model follows the workflow perfectly.

## When to skip it

A cheap reversible choice or implementation that has already been decided.

## Requirements and limits

Does not bundle provider logins or model API calls. The host supplies authorized model access. Confidence is an estimate, not a calibrated score; valid JSON does not prove the cited evidence is true.

All model selection follows the [subscription-aware policy](../model-policy.md).

## Try the offline tools

The supplied inputs are fictional examples, not actual review evidence:

```bash
python3 skills/crucible-forge/scripts/validate_verdict.py examples/reviewer-a.json
python3 skills/crucible-forge/scripts/aggregate_verdicts.py examples/reviewer-a.json examples/reviewer-b.json
```

For a real panel, use the host's authorized separate reviewers, give each the same raw question
and evidence, and save each response in the schema. Do not show reviewers one another's first drafts.
Unique names are not proof of independent review. A dissenting missing-evidence vote prevents approval. Mixed verdicts defer the decision;
unanimous FAIL rejects it. Every original panel record remains attached.

Validator exit `0` means the record is structurally valid, including its cross-field rules.
Aggregator exit `0` means PASS, `1` means FAIL or INSUFFICIENT_EVIDENCE, and `2` means invalid input
or an output-file error. `--out new-file.json` refuses to replace an existing record.
