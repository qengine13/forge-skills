# Installation

[← Collection](../README.md)

## Before you begin

Have Git, Python 3.11+, and an installed agent that can read local skills. The installer has no
third-party Python dependencies. Your normal agent subscription is separate from this collection.
Basic installation and memory recall need no model API. Optional memory adjudication uses the
subscription-authenticated Claude CLI only when explicitly enabled.

Clone the repository, then work from its root:

```bash
git clone https://github.com/qengine13/forge-skills.git
cd forge-skills
python3 --version
```

On Windows, use `py -3.11` or `python` in place of `python3` if that is how your supported Python is installed.

Replace `/path/to/project` below with an existing project directory. Quote a path containing spaces.

## Claude Code

```bash
python3 scripts/install.py --agent claude --project /path/to/project --all --dry-run
python3 scripts/install.py --agent claude --project /path/to/project --all
```

Folders are copied to `<project>/.claude/skills/`. Start a new Claude Code session in that project.
Type `/plan-hygiene` with your request, or ask Claude to use Plan Hygiene by name.

## Codex

```bash
python3 scripts/install.py --agent codex --project /path/to/project --all
```

Folders are copied to `<project>/.agents/skills/`. Start a new session in that project.
Invoke `$plan-hygiene` with your request. Host versions and account policy can affect discovery.

## Install only what you need

```bash
python3 scripts/install.py --agent claude --project /path/to/project --skill plan-hygiene --skill completion-hygiene
```

Project Forge includes Anti-Slop Gates automatically because it links to that sibling package.
Other skill folders are self-contained. Agent Memory includes its server, but requires a separate
[connection step](skills/agent-memory.md); optional advanced features have additional prerequisites.
Specialist review helpers have a bundled fallback checklist.

## Try memory without touching your data

```bash
python3 scripts/memory_demo.py
```

This uses the real server with a disposable fictional store. Follow the illustrated
[Agent Memory guide](skills/agent-memory.md) to connect your own private directory.

## Manual installation and other hosts

Copy a complete `skills/<name>/` folder to the skill directory your host documents. Do not copy only
SKILL.md: references and scripts matter. Keep Project Forge and Anti-Slop Gates as sibling folders.
For hosts without skill discovery, ask the agent to read the full SKILL.md at its path and follow it.
That manual method requires file access; scripts additionally require a Python-capable shell.
Automatic discovery outside Claude Code and Codex has not been verified by this release.

## Update without losing customizations

The installer intentionally stops if a selected destination already exists; it does not merge or
replace it. Fetch repository updates with `git pull --ff-only`, compare your installed folder with
the new version, and back up local customizations. Move the old skill folder **outside the host's
skill discovery directory**, then install the new copy. If Project Forge also installs its dependency,
handle both destination folders before rerunning. Restart the agent session.

To roll back, remove only the newly installed folder and restore your backup. To uninstall, remove
only the selected collection folders from the target project's skill directory. The installer never
adds hooks, accounts, environment variables, databases, or global settings.

## Troubleshooting

| Symptom | Check |
|---|---|
| `python3` is missing or older than 3.11 | Install a supported Python, or manually copy instruction-only skills |
| “Project directory does not exist” | Supply a real directory; the installer will not create a guessed project |
| “Existing skill(s)” | Back up and compare the named folders; nothing was copied by that attempt |
| Skill is not discoverable | Start in the intended project; verify `<skill>/SKILL.md`; restart the session; inspect host policy |
| Skill refers to another folder | Copy the whole package; Project Forge needs the sibling Anti-Slop Gates folder |
| Quality tool says NOT RUN | Install/configure that project's tool; this is not a passing result for that tool |
| Quality tool refuses native gates | Use the project's existing gate commands; do not bypass the refusal |
| Agent cannot select the newest model | Use the host's available model selector; the skill cannot grant account access |

## Host documentation

Installation conventions checked against [Claude Code skills](https://code.claude.com/docs/en/skills)
and [OpenAI skill documentation](https://developers.openai.com/codex/skills). Provider behaviour can
change; these links are the authorities for your installed host version.
