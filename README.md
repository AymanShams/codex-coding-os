# Codex Coding OS Starter

Codex Coding OS Starter is a setup pack for a first serious Codex coding project.

It gives Codex a structured path from a raw idea to a product requirements document, technical design, repo instructions, implementation plan, and first build slice.

The point is simple: do not start by asking Codex to build the app. First make the idea clear enough that the build can stay focused.

## What this pack includes

- A coding skill stack for project kickoff, PRD creation, technical documentation, implementation planning, code review, QA, and security review.
- A reusable AGENTS instruction layer for disciplined AI-assisted development.
- A first-chat prompt that guides Codex through product discovery before implementation.
- Templates for project docs, technical docs, repo instructions, handoff notes, reviews, and validation reports.
- Scripts for install, uninstall, validation, packaging, and optional external skill overlays.
- References for Codex plugins, MCPs, default skills, and external skill sources.

## Install on Windows

1. Download or clone this repo.
2. Open PowerShell inside the repo folder.
3. Run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install.ps1 -InstallGlobalAgents
```

4. Restart Codex.
5. Open a new Codex chat and paste the first prompt from `templates/first-codex-prompt.md`.

## Install without global AGENTS changes

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

## First project workflow

Use this sequence in a new Codex chat:

1. Paste the first prompt.
2. Answer Codex's project questions in batches.
3. Generate the controlled project docs.
4. Generate the technical design document.
5. Generate the implementation plan.
6. Add repo instructions.
7. Start one bounded implementation slice.
8. Validate before marking the work done.

## Included skills

The pack includes full skill folders with their references, assets, and scripts where available.

| Area | Skills |
|---|---|
| Master routing | `codex-coding-os-master`, `catalogue-router` |
| Idea to project docs | `new-project-documentation-system`, `create-prd`, `product-strategy`, `customer-journey-map`, `working-backwards` |
| Docs and artifact systems | `technical-docs-pack`, `artifact-system-designer`, `artifact-validation-workflow`, `ssot-drafter`, `ssot-auditor`, `process-docs`, `support-docs` |
| Planning and critique | `wbs-artifact-planner`, `pre-mortem`, `deep-critic`, `evidence-checker` |
| Coding discipline and architecture | `ai-coding-discipline`, `improve-codebase-architecture`, `react-best-practices`, `react-native-skills`, `composition-patterns`, `cli-creator` |
| QA and browser work | `playwright` |
| Security | `security-best-practices`, `security-threat-model`, `security-ownership-map` |
| Platform and repo tooling | `vercel-optimize`, `code-review-graph`, `vexor-cli` |
| Document intake | `doc`, `pdf` |
| External overlay routing | `external-skill-overlay-pack` |

## Included templates

The `templates/` folder covers the first prompt, project brief, PRD, app flow, tech stack, frontend rules, backend structure, security rules, implementation plan, technical design, repo documentation pack, repo instructions, handoff, review, and validation.

## Codex plugins and external skills

This pack works on its own. It also documents optional Codex plugins, MCPs, and external skill sources:

- `codex-capabilities/default-skills-reference.md`
- `codex-capabilities/plugins.manifest.json`
- `docs/codex-plugins-mcps-hooks.md`
- `THIRD_PARTY_SKILLS.md`
- `external-skills/manifest.json`
- `docs/external-skills-installation.md`

List optional external installs:

```powershell
.\scripts\install-external-skills.ps1 -List
```

Install the optional Karpathy-inspired upstream skill and apply this pack's overlay:

```powershell
.\scripts\install-external-skills.ps1 -Install forrestchang-andrej-karpathy-skills -ApplyOverlays
```

## Repository boundary

This repository is a starter pack. Project code, generated documentation, deployment settings, and connected-service configuration belong in the user's project repo.

Third-party projects stay linked to their upstream sources unless this repo explicitly says otherwise.

## Package for sharing

Run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\package.ps1
```

This validates the pack and creates `codex-coding-os-starter.zip` beside the repo folder.

## License

This repo uses a source-available licensing model:

- Personal, educational, hobby, research, evaluation, and noncommercial use are covered by `LICENSE.md`.
- Commercial or enterprise use requires a separate written commercial license. See `COMMERCIAL.md`.

This is not OSI-approved open source because the license restricts commercial use. For an OSI-approved open source release, use a license such as Apache-2.0 instead.

## Maintainer checklist

Before a release:

1. Run `.\scripts\validate-pack.ps1`.
2. Review `LICENSE.md` and `COMMERCIAL.md`.
3. Re-check third-party links and licenses in `THIRD_PARTY_SKILLS.md`.
4. Run a release-safety scan with `.release-exclusions.local.txt` if you maintain a local exclusion list.
5. Test install on a clean Windows user profile.
