# Prompt Forge

**Improve behaviour. Preserve the contract.**

[← Collection](../../README.md) · [Read the skill](../../skills/prompt-forge/SKILL.md) · [Installation](../installation.md)

## When it helps

A reusable prompt or skill needs clearer behaviour, less overhead, or defensible evaluation.

## What it can do

- Inventory constraints before changing instructions.
- Separate authored skills from templates evaluated against datasets.
- Preserve approvals, domain rules, output contracts, and success criteria while shortening.
- Define representative positive, negative, missing-input, and adversarial scenarios.
- Compare baseline and candidate under the same evaluation settings; record unresolved limits.

## Try it

In Claude Code:

```text
/prompt-forge to shorten this support-agent skill while preserving its escalation rules and output format. Define representative tests before editing; do not claim an improvement from word count alone.
```

In Codex:

```text
$prompt-forge to shorten this support-agent skill while preserving its escalation rules and output format. Define representative tests before editing; do not claim an improvement from word count alone.
```

Or ask in plain language:

> Use Prompt Forge to shorten this support-agent skill while preserving its escalation rules and output format. Define representative tests before editing; do not claim an improvement from word count alone.

## What you should receive

A behaviour contract, retained-constraint inventory, revised artifact, and an evaluation report. Without behavioural tests, the result remains a candidate.

These are expected outputs, not a promise that every model follows the workflow perfectly.

## When to skip it

A one-off question with no reusable prompt artifact.

## Requirements and limits

Requires the complete current prompt. Model upgrades must not happen halfway through a baseline/candidate comparison. Independent tests need authorized agents and an appropriate budget.

All model selection follows the [subscription-aware policy](../model-policy.md).
