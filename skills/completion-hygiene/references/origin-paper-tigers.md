# Origin: existence is not invocation

A completion review must distinguish a file's existence from working behaviour.
Check for these failure modes:

1. A category or detector is defined, but no code path ever emits it.
2. A dependency is listed as required, but no production module imports it.
3. A value is stored, but no runtime instruction ever reads it.
4. A canonical definition module exists, but instructions duplicate its values instead of importing them.
5. A distribution note says a package is available, but its release artifact is still a placeholder.

For each promise, trace the real entry point and exercise the behaviour. A file inventory alone
cannot establish that a feature works.

## Walking example (fictional)

A fictional command-line tool's plan promised three install routes: a package manager, a manual
clone, and a formula. All three files existed. Invocation testing showed:

- the package-manager route installed and ran;
- the clone instructions were read by a human and needed no code callsite;
- the formula still contained a version placeholder, had no published tap, and had no installation smoke test.

The defensible result was `CLAIM_NARROWED`: the package-manager and clone routes were supported; the
formula route was deferred. The artifact prevented the broader "distribution shipped" statement from
becoming project truth.
