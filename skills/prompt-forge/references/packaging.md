# Standalone packaging checklist

Use the target host's current supported skill format. At minimum, a skill directory contains
SKILL.md with a YAML mapping containing a nonempty name and description, followed by instructions.
Use a real YAML parser or the host's official validator; if neither is available, state validation
is unverified and do not claim a validated package. Follow the project's dependency-install authority.

- Match the directory and declared skill name; use lowercase letters, digits, and hyphens.
- Retain supported metadata; do not copy host-specific keys without checking support.
- Verify every local reference resolves inside the installed package or a declared sibling dependency.
- Keep optional scripts and resources with the skill; exercise their actual commands.
- Remove scaffolding, private paths, credentials, and instructions depending on absent machine-local files.
- Preserve permission, evidence, and scope boundaries when adapting for another host.
- Run the representative behavioural scenarios in SKILL.md; package syntax does not prove good judgment.

This checklist replaces the need for an external skill-creator helper; it does not replace a parser
or the target host's documented format.
