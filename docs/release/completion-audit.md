# Release evidence checklist

Before publishing, record these fields for the exact candidate:

```text
VERDICT: CLAIM_SUPPORTED / CLAIM_NARROWED / CLAIM_BLOCKED
SUPPORTED WORDING: <claim supported by the inspected evidence>
```

Keep each evidence boundary separate:

| Promise | Evidence required |
|---|---|
| Installable packages | Metadata, local links, fresh destination installation, collision refusal |
| Runnable local memory | Real stdio capture, restart, recall, and read-only enforcement |
| Subscription-first model work | Auth guard, disabled-model checks, explicit API fallback refusal |
| Newest eligible model selection | Host-provided availability and entitlement evidence, or honest fallback |
| Conservative decisions | Malformed-record refusal and unresolved-dissent handling |
| Quality gates | Initialization, debt detection, downward-only limits, and adapter limitations |
| Public privacy boundary | Independent semantic inspection plus exact archive and history review |
| Published revision | GitHub revision, hosted check results, downloadable archive and checksum |
| Optional backend operations | Name each exercised path; leave other paths explicitly unverified |

Use [the verification commands](../verification.md) and [privacy checklist](privacy-audit.md).
Passing local checks does not establish publication, hosted checks, or universal model compliance.
Release notes should name the observed results and remaining limits without private work history.
