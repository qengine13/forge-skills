# From a request to evidence

[← Collection](../README.md)

This is an **illustrative workflow**, not a report of a real customer project.
Imagine a small billing application needs bulk invoice downloads.

## 1. Establish the state

Ask Project Forge to inspect the repository read-only. It should locate the current download path,
existing tests, and active work. If it finds an export already implemented, it should describe the
gap instead of proposing a second implementation.

## 2. Check the plan

Ask Plan Hygiene to examine the proposal. Who can download which invoices? How much data can one
request include? What existing machinery can be reused? The result may be PRECURSOR_FIRST if
permission enforcement or the storage path is missing.

## 3. Resolve a real disagreement

If choosing between synchronous downloads and background jobs materially affects cost or reliability,
use Crucible Forge. Supply real workload evidence and the rollback constraints. Insufficient volume
or timing evidence should remain INSUFFICIENT_EVIDENCE, not become a confident recommendation.

## 4. Build with enforceable checks

Use your normal coding agent. If the project has no native equivalent, adopt Anti-Slop Gates with
an explicit review of the generated configuration and module map. Run the project's functional tests
as well as the structural checks; neither substitutes for the other.

## 5. Audit the claim

Ask Completion Hygiene to audit “bulk invoice export is shipped.” Trace the user action to the code,
exercise permissions and download behaviour, then inspect the actual release state. If only local
checks passed, the supported wording must remain local. Deployment and successful user interaction
need their own evidence.

## 6. Improve a recurring instruction

If the coding agent repeatedly skips the permissions check, use Prompt Forge to revise the reusable
instruction. Compare it against the old version on realistic requests with the same model and settings.
Retain the original constraints; shorter text alone is not proof of better behaviour.
