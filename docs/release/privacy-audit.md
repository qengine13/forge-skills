# Public distribution privacy review checklist

Apply this checklist to the exact tree and archive being published. Preserve normal author,
repository, licence, and third-party attribution. Do not include private search vocabulary or
sensitive findings in the release itself.

1. Inventory tracked files, hidden configuration, filenames, images, archives, and Git history.
2. Inspect every distributable text file semantically, including comments, examples, prompts,
   defaults, and documentation. Remove private context even when names have been anonymized.
3. Search for credentials, personal paths, email addresses, private endpoints, identifiers,
   copied records, internal measurements, and historical incident narratives. Classify matches;
   a generic word or an intentional author credit is not automatically a privacy leak.
4. Run the package validator and a dedicated secret scanner. Review candidate matches without
   printing secrets into public reports. Automated scans do not replace semantic inspection.
5. Request an independent review of the same candidate. Verify findings against actual files.
6. Create the archive from the inspected Git tree. Compare its full file list and contents,
   check that local stores and `.git` are absent, and install it in a disposable destination.
7. Inspect public branches, tags, release assets, and accessible older revisions. Changing the
   branch tip alone does not remove content from earlier public artifacts.
8. Record the exact revision, checks, omissions, and archive checksum in release notes.

Privacy review is bounded inspection, not proof against every possible identifying detail.
Previously downloaded copies cannot be recalled through a repository update.
