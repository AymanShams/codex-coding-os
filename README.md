# Codex Coding OS Starter

This repo is a one-install starter pack for a first serious Codex coding project.

It gives Codex a disciplined workflow for turning an idea into a PRD, controlled project docs, a technical build plan, repo instructions, and then a first implementation slice.

## What This Pack Does

- Installs a coding-only skill set into the user's Codex skills folder.
- Adds a generic AGENTS instruction layer for safe AI-assisted coding.
- Guides the first chat from idea intake to PRD, TDD, repo docs, and first build slice.
- Keeps third-party skills as links and references unless they are built into this pack.
- Includes optional overlay notes for external skills that inspired the workflow.
- Avoids company, patient, client, and private workspace context.

## Install On Windows

1. Download or clone this repo.
2. Open PowerShell inside the repo folder.
3. Run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install.ps1 -InstallGlobalAgents
```

4. Restart Codex.
5. Open a new Codex chat and paste the first prompt from `templates/first-codex-prompt.md`.

## Install Without Global AGENTS Changes

If you only want the skills copied and do not want to modify `~\.codex\AGENTS.md`, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install.ps1
```

Then start Codex from a project folder that has its own `AGENTS.md`, or explicitly invoke:

```text
$codex-coding-os-master
```

## Uninstall

Run this from the repo folder:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\uninstall.ps1
```

The uninstall script removes the skills installed by this pack and removes the global AGENTS block if it was added by the installer.

## First Chat Workflow

The first chat should not start with "build the app."

Use this flow:

1. Paste the first prompt.
2. Let Codex ask project-defining questions.
3. Answer in batches.
4. Let Codex create the seven controlled docs.
5. Let Codex create the TDD build plan.
6. Let Codex create repo instructions.
7. Only then start the first implementation slice.

## Included Skills

| Skill | Purpose |
|---|---|
| `codex-coding-os-master` | Main router for first-project coding work |
| `first-project-prd-workflow` | Turns a raw idea into scoped product docs |
| `source-locked-docs-workflow` | Creates source-faithful docs and prevents generic filler |
| `ai-coding-discipline-pack` | Keeps AI coding bounded, simple, surgical, and verified |
| `repo-agent-instructions-pack` | Creates root and scoped AGENTS files plus handoff notes |
| `simplify-review-gate` | Reviews diffs for reuse, quality, and efficiency |
| `security-prelaunch-gate` | Applies minimum viable security checks before launch |
| `frontend-qa-gate` | Runs browser and UI verification after frontend work |
| `external-skill-overlay-pack` | Captures local improvements to third-party skills as overlays |

## Included Templates

The templates in `templates/` cover the first prompt, project brief, PRD, app flow, tech stack, frontend rules, backend structure, security rules, implementation plan, technical design, full repo documentation pack, repo instructions, handoff, review, and validation.

## Package For Sharing

Run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\package.ps1
```

This validates the pack and creates `codex-coding-os-starter.zip` beside the repo folder.

## What Is Not Included

- Private company context.
- Personal memories.
- Credentials, tokens, API keys, or production environment values.
- Full copies of third-party skills unless their license and provenance are separately reviewed.
- Plugin cache files from a private Codex setup.

## License Position

This repo uses a source-available licensing model:

- Personal, educational, hobby, research, evaluation, and noncommercial use are covered by the public license in `LICENSE.md`.
- Commercial or enterprise use requires a separate written commercial license. See `COMMERCIAL.md`.

This is not OSI-approved open source because commercial restriction is not open source under the Open Source Definition. If you want true open source instead, switch to Apache-2.0.

## Maintainer Notes

Before publishing publicly:

1. Run `.\scripts\validate-pack.ps1`.
2. Optionally create `.private-terms.local.txt` with one private term per line and rerun validation.
3. Review `LICENSE.md` with counsel.
4. Confirm all third-party references in `THIRD_PARTY_SKILLS.md`.
5. Remove any private names from examples.
6. Test install on a clean Windows user profile.
