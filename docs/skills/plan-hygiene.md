# Plan Hygiene

**Discover the expensive mistake early.**

[← Collection](../../README.md) · [Read the skill](../../skills/plan-hygiene/SKILL.md) · [Installation](../installation.md)

## When it helps

You are about to commit meaningful effort to a feature, migration, workflow, or new instruction set.

## What it can do

- Define a goal, scope, constraints, and observable success criteria.
- Check existing code and work in flight before proposing new work.
- Test dependencies, measurement assumptions, project doctrine, and prior decisions.
- Challenge the plan and identify prerequisites or reasons to stop.
- Produce a preflight artifact with explicit completion gates.

## Try it

In Claude Code:

```text
/plan-hygiene on a proposal to add bulk invoice export. Check existing export paths, permissions, data volume, dependencies, and proof of success. Do not implement it.
```

In Codex:

```text
$plan-hygiene on a proposal to add bulk invoice export. Check existing export paths, permissions, data volume, dependencies, and proof of success. Do not implement it.
```

Or ask in plain language:

> Use Plan Hygiene on a proposal to add bulk invoice export. Check existing export paths, permissions, data volume, dependencies, and proof of success. Do not implement it.

## What you should receive

PROCEED, RE_SCOPE, PRECURSOR_FIRST, AMEND_FIRST, or KILL, with supporting evidence and completion gates.

These are expected outputs, not a promise that every model follows the workflow perfectly.

## When to skip it

A typo fix, simple lookup, or narrow routine change.

## Requirements and limits

Needs the actual objective and access to relevant artifacts. A plan that looks sensible is not evidence that dependencies exist.

All model selection follows the [subscription-aware policy](../model-policy.md).
