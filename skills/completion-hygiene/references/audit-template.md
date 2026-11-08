# Completion audit template

```markdown
# Completion audit — <scope>

**Date:** YYYY-MM-DD
**Driving plan or request:** <path or exact user request>
**Claim being audited:** <exact completion claim>
**Audit mode:** independent review / subagent review / self-check

## Evidence boundaries

- Local tree: <branch, revision, dirty state>
- Remote state: <checked and result / not checked>
- Hosted checks: <checked and result / not checked>
- Runtime or browser: <checked and result / unavailable>

## Promise matrix

| Promise | Source | Artifact evidence | Invocation or behaviour evidence | Status |
|---|---|---|---|---|
| <promise> | <file:line or request> | <file:line> | <entry point, command, test, or external proof> | PROVEN / EXISTS_UNPROVEN / MISSING / NOT_APPLICABLE |

## Verification

| Command or check | Result | Claim supported |
|---|---|---|
| <exact check> | <pass, fail, or unavailable> | <specific claim> |

## Gaps and disposition

- <gap>: remediate / narrow claim / amend plan / block

## Verdict

CLAIM_SUPPORTED / CLAIM_NARROWED / CLAIM_BLOCKED / PLAN_AMENDMENT_REQUIRED

**Supported wording:** <exact wording that may now be used>
**Remaining risks:** <list or none>
```
