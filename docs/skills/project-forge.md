# Project Forge

**Find the next useful move.**

[← Collection](../../README.md) · [Read the skill](../../skills/project-forge/SKILL.md) · [Installation](../installation.md)

## When it helps

A fresh agent enters an existing project and needs to establish what is real before acting.

## What it can do

- Read local operating rules, current-state records, and the relevant source files.
- Inspect working-tree state and preserve work already in progress.
- Identify the current blocker and recommend one next action.
- Route to available specialists by capability; adopt the quality checks when appropriate.
- Establish minimal project-state documents only when the user authorizes adoption.

## Try it

In Claude Code:

```text
/project-forge to inspect this repository and recommend the next useful action. Stay read-only; distinguish verified state from recorded plans.
```

In Codex:

```text
$project-forge to inspect this repository and recommend the next useful action. Stay read-only; distinguish verified state from recorded plans.
```

Or ask in plain language:

> Use Project Forge to inspect this repository and recommend the next useful action. Stay read-only; distinguish verified state from recorded plans.

## What you should receive

A short project/stage/current/recommendation/evidence/boundaries report. No edits for this read-only request.

These are expected outputs, not a promise that every model follows the workflow perfectly.

## When to skip it

A standalone question or a task whose current context is already established.

## Requirements and limits

Requires readable project files. The installer includes Anti-Slop Gates as a sibling dependency. Missing project-state documents are not permission to invent them.

All model selection follows the [subscription-aware policy](../model-policy.md).
