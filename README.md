# Codex Coding OS Starter

Codex Coding OS Starter is the installable package name for a full Codex Coding OS.

It gives Codex a structured operating system for software work: idea intake, product requirements, technical design, repo instructions, implementation planning, coding discipline, UI and documentation polish, security review, incident response, RCA, and validation.

The point is simple: do not treat AI coding as a blank prompt. Give Codex durable rules, reusable skills, clear templates, and a bounded workflow before it touches code.

[Start here: five-minute setup and first project](docs/getting-started.md)

The operating principles are in [docs/philosophy.md](docs/philosophy.md). Review assurance rules are in [docs/review-doctrine.md](docs/review-doctrine.md).

## What this pack includes

- A coding skill stack for project kickoff, PRD creation, technical documentation, implementation planning, code review, QA, design artifacts, security review, incident handling, RCA, and public-facing documentation polish.
- A reusable AGENTS instruction layer for disciplined AI-assisted development.
- A first-chat prompt that guides Codex through product discovery before implementation.
- Templates for project docs, technical docs, repo instructions, handoff notes, fresh-context reviews, and validation reports.
- Automated project session continuity for start, resume, exact-next-action checks, session-boundary decisions, and persistent handoffs.
- Scripts for install, uninstall, validation, packaging, and optional external skill overlays.
- References for Codex plugins, MCPs, local tools, default skills, command rules, and external skill sources.

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

Preview the install without changing files:

```powershell
.\scripts\install.ps1 -InstallGlobalAgents -DryRun
```

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

## Install on macOS or Linux

From the repo folder:

```bash
chmod +x ./scripts/install.sh ./scripts/uninstall.sh
./scripts/install.sh --install-global-agents
```

Preview the install:

```bash
./scripts/install.sh --install-global-agents --dry-run
```

## Uninstall

Run this from the repo folder:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\uninstall.ps1
```

The uninstall script removes the skills installed by this pack and removes the global AGENTS block if it was added by the installer.

If you installed with custom paths, pass the same paths to uninstall:

```powershell
.\scripts\uninstall.ps1 -SkillsRoot "<skills-root>" -CodexHome "<codex-home>"
```

On macOS or Linux:

```bash
./scripts/uninstall.sh
```

## Tested matrix

| Environment | Status |
|---|---|
| Windows PowerShell 5.1+ | Primary supported path |
| PowerShell 7 on Windows | Supported by the PowerShell scripts |
| Ubuntu bash | Validated by GitHub Actions install/uninstall smoke tests |
| macOS bash | Validated by GitHub Actions install/uninstall smoke tests |

## Default coding workflow

Use this sequence in a new Codex chat or when taking over an existing repo:

1. Paste the first prompt.
2. Answer Codex's project questions in batches.
3. Generate the controlled project docs.
4. Generate the technical design document.
5. Generate the implementation plan.
6. Add repo instructions.
7. Add current delivery state and the project session-start gate.
8. Start one bounded implementation slice only when the workflow manifest permits coding.
9. Review, test, and validate before marking the work done.

## When to invoke the full project workflow

Use the full workflow for a new product, an unclear existing repo, architecture changes, shared or production-facing behavior, sensitive data, or work where incorrect assumptions would create material rework or risk.

For a small, reversible, well-specified change, keep the pack installed but use the narrowest relevant skill, preserve source truth, and run proportionate validation. Do not manufacture a full project-documentation cycle for a trivial edit.

Documentation completeness is stage-bounded. The existing Stage 0 to Stage 6 model in `.agents/skills/new-project-documentation-system/references/documentation-stage-map.md` defines what must be filled and what remains not due.

## Included skills

The pack includes full skill folders with their references, assets, and scripts where available.

| Area | Skills |
|---|---|
| Master routing and continuity | `codex-coding-os-master`, `catalogue-router`, `project-session-continuity` |
| Idea to project docs | `new-project-documentation-system`, `create-prd`, `product-strategy`, `customer-journey-map`, `working-backwards` |
| Docs and artifact systems | `technical-docs-pack`, `artifact-system-designer`, `artifact-validation-workflow`, `ssot-drafter`, `ssot-auditor`, `process-docs`, `support-docs` |
| Planning and critique | `wbs-artifact-planner`, `pre-mortem`, `deep-critic`, `evidence-checker`, `grill-me`, `grill-with-docs` |
| Coding discipline and architecture | `ai-coding-discipline`, `improve-codebase-architecture`, `react-best-practices`, `react-native-skills`, `composition-patterns`, `cli-creator`, `quality-improvement-problem-solving`, `quant-review` |
| Design and writing quality | `codex-design-artifacts`, `humanizer`, `storyscope-structural-audit` |
| QA and browser work | `playwright` |
| Security and incident readiness | `security-best-practices`, `security-threat-model`, `security-ownership-map`, `defensive-security-checklist`, `crisis-command-center` |
| Platform and repo tooling | `vercel-optimize`, `code-review-graph`, `vexor-cli`, `chat-export-capability-miner` |
| Document intake | `doc`, `pdf` |
| External overlay routing | `external-skill-overlay-pack` |

## Included templates

The `templates/` folder covers the first prompt, project brief, PRD, app flow, tech stack, frontend rules, backend structure, security rules, implementation plan, technical design, architecture decision records, repo documentation pack, repo instructions, handoff, review, and validation.

## Codex plugins and external skills

This pack works on its own. It also documents optional Codex plugins, MCPs, local tools, and external skill sources:

- `codex-capabilities/default-skills-reference.md`
- `codex-capabilities/plugins.manifest.json`
- `codex-capabilities/tools.manifest.json`
- `docs/codex-plugins-mcps-hooks.md`
- `docs/codex-rules.md`
- `docs/mcp-review-checklist.md`
- `docs/system-scope.md`
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

Every external source uses the same provenance fields in `external-skills/manifest.json`. The current installable source is pinned to a reviewed commit. Any future unpinned installable source will stop by default unless the user explicitly passes `-AllowUnpinned` after reviewing upstream.

## Optional Codex rules

This repo includes command approval rule templates in `.codex/rules/`.

They are not installed automatically because they affect command execution outside the sandbox. Review `docs/codex-rules.md`, then copy `.codex/rules/default.rules` into a user or trusted project rules layer.

## Repository boundary

This repository is an installable Coding OS package. Project code, generated documentation, deployment settings, and connected-service configuration belong in the user's project repo.

Third-party projects stay linked to their upstream sources unless this repo explicitly says otherwise.

## Provenance and attribution

Skills and source material not created for this pack are intended to come from public/open-source sources. We made a good-faith effort to preserve source links, license notes, and attribution in `THIRD_PARTY_SKILLS.md`, bundled license files, and `external-skills/manifest.json`.

If a source, attribution, or license note is missing, please open an issue or pull request with the affected file, source URL, and proposed correction.

## Package for sharing

Run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\package.ps1
```

This validates the pack and creates `codex-coding-os-starter.zip` beside the repo folder.

When run from a Git clone, packaging requires tracked files to match `HEAD` and archives the committed revision so local untracked files cannot enter the release.

`pack.manifest.json#version` is the sole package release version. Release changes are recorded in `CHANGELOG.md`.

## License

This repo uses a source-available licensing model:

- Personal, educational, hobby, research, evaluation, and noncommercial use are covered by `LICENSE.md`.
- Commercial or enterprise use requires a separate written commercial license. See `COMMERCIAL.md`.
