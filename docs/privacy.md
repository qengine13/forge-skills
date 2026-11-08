# Public-package privacy review

[← Collection](../README.md) · [Verification](verification.md)

The distribution contains code, instructions, fictional examples, and documentation. It does not
need the author's repositories, conversations, credentials, or memory store to run. Public GitHub
identity and required licensing attribution are intentionally retained.

## Review method

A release review must cover more than a short keyword list:

1. Inventory every distributable file, including hidden configuration, assets and filenames.
2. Read imported code and prose for personal context: comments, docstrings, incident narratives,
   real examples, corpus measurements, machine assumptions and embedded paths.
3. Match local project-name vocabulary privately; classify ordinary-word collisions manually.
   Never ship the private vocabulary or captured examples in the audit report.
4. Scan for credentials using a dedicated detector, then inspect candidate locations without
   reproducing sensitive values. Environment-variable names and fictional fixtures are not keys.
5. Inspect published branches, tags and reachable history. Deleting text from the latest revision
   does not remove it from an earlier commit.
6. Verify the final archive, not just the working directory, and exercise the installed package.

The [release checklist](release/privacy-audit.md) defines the inspection steps. Release notes
record the exact revision and observed results. Automated searches cannot prove that no
conceivable identifying detail exists. Independent semantic review adds coverage; it is not a
mathematical guarantee. Rewriting public history cannot recall copies already fetched by others
or guarantee removal from third-party caches.

## Your data after installation

Keep memory outside your Git checkout. Do not commit generated notes, indexes, server connection
credentials, transcripts, or machine-specific configuration. The basic server creates a new store
only at the directory you explicitly select; it does not migrate the author's or your other stores.
Optional history-import tools act on your selected local history only when invoked.

When reporting a bug, use fictional notes and a disposable directory. Do not attach your live
memory ledger, database dump, provider configuration, or unredacted conversation history.
