# Completion Hygiene

**Prove what actually works.**

[← Collection](../../README.md) · [Read the skill](../../skills/completion-hygiene/SKILL.md) · [Installation](../installation.md)

## When it helps

An agent is about to call a feature finished, shipped, deployed, or ready.

## What it can do

- Recover the original promises and verify their artifacts.
- Trace real entry points to the implementation.
- Separate existence from reachability and exercised behaviour.
- Separate local checks, review, publication, merge, deployment, and user-visible evidence.
- Emit a literal verdict, supported wording, evidence boundaries, and a promise matrix.

## Try it

In Claude Code:

```text
/completion-hygiene to audit the claim “invoice export is shipped.” Read the original plan, trace the real download path, and check the available release evidence. Keep this audit read-only.
```

In Codex:

```text
$completion-hygiene to audit the claim “invoice export is shipped.” Read the original plan, trace the real download path, and check the available release evidence. Keep this audit read-only.
```

Or ask in plain language:

> Use Completion Hygiene to audit the claim “invoice export is shipped.” Read the original plan, trace the real download path, and check the available release evidence. Keep this audit read-only.

## What you should receive

VERDICT and SUPPORTED WORDING, followed by promises classified PROVEN, EXISTS_UNPROVEN, MISSING, or NOT_APPLICABLE.

These are expected outputs, not a promise that every model follows the workflow perfectly.

## When to skip it

An ordinary progress update with no completion claim.

## Requirements and limits

Needs a bounded claim and its supporting artifacts. Unavailable remote or runtime evidence stays unverified. A passing unit test alone does not establish deployment.

All model selection follows the [subscription-aware policy](../model-policy.md).
