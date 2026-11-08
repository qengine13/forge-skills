# Adoption review without external skills

These are required human/agent judgments; the engine cannot make them.

## Code review
Read the real entry points and existing checks. Inspect duplicate/dead code, dependency health,
circular imports, security-sensitive boundaries, and unchecked failure paths. Rank concrete
findings with file/line evidence in `.slopgates/CLEANUP-PLAN.md`. Do not auto-fix during the audit.
Use available project tools and report missing tools as NOT RUN.

## Architecture review
Review the drafted `.slopgates/modules.toml`: confirm owners, source roots, public entry files,
allowed import directions, and shared helpers. An observed import is not proof it should be allowed.
Record reasons for changes. Write `modules.target.toml` and `TARGET-MAP.md` with small migration
steps, their benefit, risk, tests, and rollback. Obtain owner approval for each migration step.
The current map describes current permitted boundaries; the target describes proposed boundaries.
Never rewrite the project merely to fit the tool. Use `target` to compare the two maps.

For HTML-only projects with no importable modules, record the architecture pass as not applicable
in the cleanup plan. Do not invent modules, target maps, or migrations merely to satisfy this tool.
When native project checks are authoritative, inspect the project's existing architecture and
boundary documentation instead of requiring `.slopgates/` files. Record proposed changes there.
