# Verification status

[← Collection](../README.md) · [Privacy review](privacy.md)

Checks of executable tools and checks of an agent's behaviour are different evidence. This
release does not claim perfect model compliance, universal compatibility, or a productivity gain.

## Local executable checks

The release suite contains **34 tests**, covering:

- Complete seven-skill installation into temporary Claude Code and Codex projects; dependency
  inclusion, collision refusal, dry runs, missing-project errors, and symlink refusal.
- Quality-gate initialization, new debt detection, downward-only limits, and native-check refusal.
- Decision-record validation, unresolved dissent, duplicate reviewers, and non-overwriting output.
- Memory startup, tool discovery, empty stores, capture/restart/recall, supersession history,
  read-only enforcement, malformed input, duplicate IDs, same-project correction, lock contention,
  explicit-home history decoding, backend-free operator help, and refusal of inherited database or per-call database overrides.
- Portable audit risk paths and actual review-command evidence, using fictional commits.
- Disabled model calls, refusal of API-auth fallback, and credential-variable removal (mocked
  subprocess boundary; these tests do not contact a provider).
- UTF-8 portability, package metadata, syntax, links, identical shared model policies, and privacy
  patterns in code and hidden configuration. Memory data and unreviewed binary assets are refused.

Run from the collection root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/validate_package.py
.venv/bin/python scripts/memory_demo.py
```

On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.
The disposable demo exercises the real server in separate processes with fictional notes.

## Runtime verification procedure

For a host-level trial, install Agent Memory into a fresh disposable project. Connect using the
host's subscription login, allow stats, explicitly authorized capture, and search, then inspect
the fictional note independently in the disposable ledger. Record the actual model-selection
method; a configured-model trial does not prove newest-entitled-model discovery.

For PostgreSQL, use an empty disposable database. Exercise schema initialization, capture,
search, recent history, and lexical Heal proposals. Remove the trial database afterward. These
checks do not cover every advanced maintenance operation.

Use the [scenario checklist](release/scenario-review.md) to evaluate instruction-only skills
independently. Verify claimed evidence against the artifacts rather than trusting model summaries.

## Hosted checks and downloads

The workflow runs on Linux, macOS, and Windows with Python 3.11 and 3.12. Consult the
[exact-revision workflow results](https://github.com/qengine13/forge-skills/actions/workflows/check.yml)
and [release notes](https://github.com/qengine13/forge-skills/releases) for published results.
An earlier green run does not certify a later commit. The release archive is created from the
committed tree and includes a SHA-256 checksum; caches and local stores are excluded.

## Limits

- Installer tests verify copied files; host discovery can vary with version and account policy.
- The suite exercises selected quality adapters and memory paths, not every supported syntax or
  optional backend. Live embeddings, full history migration, the browser console, and every
  Dream/Heal apply scenario have not been established by this release's end-to-end trials.
- The basic file server is a local single-user tool, not a multi-tenant or network security boundary.
  File and PostgreSQL writes are not a distributed transaction; inspect reported errors.
- Advanced operator scripts have their own flags and can mutate data when invoked directly. They
  do not inherit the managed launcher's read-only restrictions.
- Doctrine-audit risk detection uses general path-component heuristics. Project-specific rules
  need local adaptation; it is not a universal compliance checker. Review evidence recognizes
  standard Codex review output in supported session formats; another reviewer needs local
  adaptation. Findings are suggestions based on session text, not proof that a review ran.
  Shell substitutions and complex shell syntax are not interpreted as proof.
- The decision aggregator validates records and preserves dissent; it does not prove evidence truth
  or reviewer independence. Latest model selection depends on what the host can verify and select.
- Privacy scans and independent semantic review are bounded inspection, not a guarantee against
  every possible identifying detail. Public authorship and licensing credit remain intentional.
