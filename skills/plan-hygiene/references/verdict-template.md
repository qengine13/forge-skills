# Pre-flight verdict template

```markdown
# Pre-flight verdict — <scope>

**Date:** YYYY-MM-DD
**Trigger:** <why plan-hygiene fired>
**Verdict:** PROCEED / RE_SCOPE / PRECURSOR_FIRST / AMEND_FIRST / KILL

## Goal and boundaries

- Goal: <one sentence>
- Must do: <list>
- Must not do: <list>
- Success: <observable result>
- Failure modes: <at least two>

## Gate verdicts

- Gate 0 — Trigger: FIRES
- Gate 1 — Goal and contract: PASS / NEEDS_INPUT
- Gate 2 — Substrate: PASS / RE_SCOPE / PRECURSOR_FIRST / KILL / NOT_APPLICABLE
- Gate 3 — Measurement: PASS / RE_SCOPE / KILL / NOT_APPLICABLE
- Gate 4 — Doctrine and architecture: PASS / AMEND_FIRST / KILL
- Gate 5 — Prior decisions: PASS / KILL / LIMITED_WITH_REASON
- Gate 6 — Challenge: PASS / SCOPE_CHANGE / KILL / SELF_CHECK / SKIP_WITH_REASON

## Evidence

| Claim or requirement | Evidence | Effect on plan |
|---|---|---|
| <item> | <file:line, command result, source, or user fact> | <result> |

## Completion gates

- [ ] <core behaviour and exact proof>
- [ ] <hard dependency or risk boundary and proof>
- [ ] <external or user-visible surface and proof, or not applicable>

## Estimate

- Agent effort: <hours>
- Calendar span: <range>

## Ratification

- Required: yes / no
- Separately attributable decision: <evidence or pending>

## Decision

<next authorized action or blocker>
```
