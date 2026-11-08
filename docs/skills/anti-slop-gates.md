# Anti-Slop Gates

**Make quality a check you can run.**

[← Collection](../../README.md) · [Read the skill](../../skills/anti-slop-gates/SKILL.md) · [Installation](../installation.md)

## When it helps

Your project needs enforceable limits on accumulating code and markup problems.

## What it can do

- Initialize a new project or adopt an existing one without requiring all old debt to disappear on day one.
- Run Python and Node/TypeScript tool adapters, plus built-in structural checks.
- Check duplicate code, unexplained suppressions, public documentation, module boundaries, and prior-art receipts.
- Check HTML structure, class naming, and configurable debt counters.
- Lock in lower debt counts after cleanup; reject increases and new unapproved names.

## Try it

In Claude Code:

```text
/anti-slop-gates to assess adopting checks in this repository. Inspect existing checks first. Explain the proposed configuration before setting it up.
```

In Codex:

```text
$anti-slop-gates to assess adopting checks in this repository. Inspect existing checks first. Explain the proposed configuration before setting it up.
```

Or ask in plain language:

> Use Anti-Slop Gates to assess adopting checks in this repository. Inspect existing checks first. Explain the proposed configuration before setting it up.

## What you should receive

A .slopgates configuration and baseline after authorized setup; check exits 0 for a passing configured gate, 1 for findings, and 2 for setup/refusal errors.

These are expected outputs, not a promise that every model follows the workflow perfectly.

## When to skip it

A project already carrying the native gate markers, or a request to judge visual taste.

## Requirements and limits

Python 3.11+. Tool adapters require the project’s own tools. A missing optional tool is NOT RUN, not a pass. HTML structure checking does not parse React components as HTML. Existing debt is initially grandfathered.

All model selection follows the [subscription-aware policy](../model-policy.md).

## Run the checks directly

From the collection checkout (replace `/path/to/project` with a real directory):

```bash
python3 skills/anti-slop-gates/scripts/slopgate.py init --root /path/to/project
python3 skills/anti-slop-gates/scripts/slopgate.py check --root /path/to/project
python3 skills/anti-slop-gates/scripts/slopgate.py report --root /path/to/project
```

Use `adopt` instead of `init` for an existing codebase, then complete the
[adoption review](../../skills/anti-slop-gates/references/adoption-review.md). After reducing debt,
run `update` to lock in the lower count. Never reinitialize to hide a regression.

The engine has no Python package dependencies. Its format, lint, and type adapters use installed
project tools; install and configure those tools through your normal project workflow.

## Keep existing project checks

Inspect the project's existing checks before setup. To make the refusal automatic, add a plain
`.native-quality-gates` file at the project root explaining which commands to use. The bundled
runner exits with status 2 when it finds that marker; it does not replace or run those checks.
