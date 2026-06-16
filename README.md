# Codex Coding OS

[![Validate pack](https://github.com/AymanShams/codex-coding-os/actions/workflows/validate.yml/badge.svg)](https://github.com/AymanShams/codex-coding-os/actions/workflows/validate.yml)
[![License: MPL-2.0](https://img.shields.io/badge/License-MPL--2.0-brightgreen.svg)](LICENSE.md)
[![Stars](https://img.shields.io/badge/dynamic/json?label=stars&query=stargazers_count&url=https%3A%2F%2Fapi.github.com%2Frepos%2FAymanShams%2Fcodex-coding-os&logo=github)](https://github.com/AymanShams/codex-coding-os/stargazers)
[![Last commit](https://img.shields.io/badge/last%20commit-view%20history-555?logo=github)](https://github.com/AymanShams/codex-coding-os/commits/main)

Independent open-source project. Not affiliated with, endorsed by, or sponsored
by OpenAI.

Codex Coding OS is an installable workflow pack for Codex: a spec-first,
skills-mediated control system for AI-assisted software development.

It is designed for builders who do not want Codex to start from a vague prompt and
drift into unreviewable code. It gives Codex a structured path from idea intake to
source-locked project documents, technical design, repo instructions, bounded
implementation, review, validation, and handoff.

[Start here: five-minute setup and first project](docs/getting-started.md)

Operating principles live in [docs/philosophy.md](docs/philosophy.md). Review rules
live in [docs/review-doctrine.md](docs/review-doctrine.md).

Third-party sources, adapted materials, and optional upstream installs are tracked
in [THIRD_PARTY_SKILLS.md](THIRD_PARTY_SKILLS.md) and
[external-skills/manifest.json](external-skills/manifest.json).

## Inspect before global changes

Preview the Windows install without changing files:

```powershell
.\scripts\install.ps1 -InstallGlobalAgents -DryRun
```

Install the skills without modifying `~\.codex\AGENTS.md`:

```powershell
.\scripts\install.ps1
```

Global Codex instructions are installed only when you pass `-InstallGlobalAgents`.

## Fast path

1. Install the pack.
2. Restart Codex.
3. Open a new Codex chat.
4. Paste `templates/first-codex-prompt.md` with your idea filled in.
5. Let Codex gather decisions and create the project documents before coding.

## Who this is for

- Non-technical founders who want Codex to move from idea to PRD, technical plan,
  and implementation without skipping the questions that prevent rework.
- Technical builders who want reusable skills, templates, review gates, and
  validation scripts around AI-assisted coding.
- Teams that need a lightweight public-release-ready pack they can inspect, fork,
  and adapt without importing private project context.

Use the full workflow for new products, unclear repositories, architecture changes,
shared behavior, sensitive data, or work where wrong assumptions would be expensive.
Use the lighter path for small, reversible, already-specified edits.

## What this solves

AI coding usually fails before the code is written. The common failure pattern is
vague intent, invented product decisions, lost context, uncontrolled scope, and
completion claims that are not backed by tests or review.

This pack turns that into a controlled workflow:

- define the project before implementation starts
- separate controlling sources from reference material
- stop when material decisions are missing
- use existing skills instead of giant prompts
- keep one accountable execution context per bounded slice
- use fresh-context review for material work
- verify before calling work complete
- preserve handoff state across chats

## Concrete example

A non-technical founder asks Codex:

```text
Build a follow-up workflow for clinics.
```

Without a structured workflow, Codex may quietly assume:

- who creates the follow-up
- who owns and approves it
- which users can see each record
- what happens when contact details or required data are missing
- which statuses exist
- what needs audit logging
- what counts as done

That can produce working code for the wrong workflow.

With Codex Coding OS, the first output is not code. Codex first has to surface:

- open questions and assumptions
- roles and permissions
- workflow states
- data and source-of-truth assumptions
- security and privacy constraints
- the first small implementation slice
- validation checks before completion

The goal is not more paperwork. The goal is to stop vague intent from becoming
confident but wrong code.

## What this pack includes

- Full local skills for project kickoff, PRDs, documentation systems, implementation
  planning, coding discipline, code review, QA, security review, incident handling,
  root-cause analysis, and writing quality.
- A master routing skill for starting from an idea or an existing repo.
- A reusable `AGENTS.md` layer and scoped AGENTS templates.
- A first-chat prompt that guides Codex through project discovery before coding.
- Templates for project docs, TDDs, repo instructions, handoffs, reviews,
  worktree-lane contracts, and validation reports.
- Session-continuity tooling for non-trivial or multi-session projects.
- Install, uninstall, validation, packaging, and optional external-skill scripts.
- References for Codex plugins, MCPs, local tools, default skills, command rules,
  and external skill sources.
- Public repo hygiene files: security reporting, contribution guidance, manifest
  schema, release checklist, and license FAQ.

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

The uninstall script removes the skills installed by this pack and removes the
global AGENTS block if the installer added it.

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

## Default workflow

A new project normally follows this sequence:

1. Paste the first prompt.
2. Answer Codex's material-decision questions in batches.
3. Create the workflow manifest.
4. Create the controlled project docs.
5. Create the TDD and record significant architecture decisions as ADRs when needed.
6. Add repo documentation and AGENTS instructions.
7. Add current delivery state, handoff structure, and the session-start gate.
8. Start one bounded implementation slice only when the workflow manifest permits coding.
9. Review, test, and validate before marking work done.

Use the full workflow for a new product, an unclear existing repo, architecture
changes, shared or production-facing behavior, sensitive data, or work where wrong
assumptions would create material rework or risk.

For a small, reversible, well-specified change, keep the pack installed but use the
narrowest relevant skill and normal repo validation. Do not manufacture a full
project-documentation cycle for a trivial edit.

Session continuity is recommended for all non-trivial or multi-session projects.
For trivial, reversible edits, use the narrowest relevant skill and normal repo checks.

Advanced parallel worktree lanes are available for material or high-risk coding
after the workflow manifest permits coding. The default is manual: Codex creates
worktrees and paste-ready prompts, and the user opens each lane thread. See
`docs/parallel-worktree-doctrine.md`.

## Included skills

The pack includes full skill folders with references, assets, and scripts where
available.

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

The `templates/` folder covers the first prompt, project brief, PRD, app flow,
tech stack, frontend rules, backend structure, security rules, implementation plan,
technical design, ADRs, repo documentation, AGENTS instructions, handoff, review,
and validation.

## Codex plugins and external skills

This pack works on its own. It also documents optional Codex plugins, MCPs, local
tools, and external skill sources:

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

The `codex-capabilities/` folder is a human and operator reference for routing,
setup, and capability-boundary decisions. It is not meant to teach Codex its own
default skills.

List optional external installs:

```powershell
.\scripts\install-external-skills.ps1 -List
```

Install the optional Karpathy-inspired upstream skill and apply this pack's overlay:

```powershell
.\scripts\install-external-skills.ps1 -Install forrestchang-andrej-karpathy-skills -ApplyOverlays
```

Every external source uses the same provenance fields in `external-skills/manifest.json`.
The current installable source is pinned to a reviewed commit. Future unpinned
installable sources stop by default unless the user explicitly passes `-AllowUnpinned`
after reviewing upstream.

## Optional Codex rules

This repo includes command approval rule templates in `.codex/rules/`.

They are not installed automatically because they affect command execution outside
the sandbox. Review `docs/codex-rules.md`, then copy `.codex/rules/default.rules`
into a user or trusted project rules layer.

## Validation

Common release checks:

```powershell
.\scripts\validate-pack.ps1
.\tests\install-uninstall-smoke.ps1
python .\tests\workflow-gates-smoke.py
python .\tests\worktree-lanes-smoke.py
.\scripts\package.ps1
```

GitHub Actions runs the Windows validation path and Bash smoke tests on Ubuntu and
macOS.

Before public promotion, run the stricter history scan after installing `gitleaks`
and `trufflehog`:

```powershell
.\scripts\release-safety-scan.ps1 -RequireExternalScanners -ScanGitHistory
```

## Repository boundary

This repository is an installable Coding OS package. Project code, generated
documentation, deployment settings, and connected-service configuration belong in
the user's project repo.

Third-party projects stay linked to their upstream sources unless this repo explicitly
says otherwise.

Codex Coding OS does not guarantee correctness. It makes software work more
traceable, reviewable, bounded, and honest about what remains unverified.

## Provenance and attribution

Skills and source material not originally created for this pack are intended to
preserve source links, license notes, and attribution in `THIRD_PARTY_SKILLS.md`,
bundled license files, and `external-skills/manifest.json`.

If a source, attribution, or license note is missing, open an issue or pull request
with the affected file, source URL, and proposed correction.

## Package for sharing

Run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\package.ps1
```

This validates the pack and creates `codex-coding-os.zip` beside the repo
folder.

When run from a Git clone, packaging requires tracked files to match `HEAD` and
archives the committed revision so local untracked files cannot enter the release.

`pack.manifest.json#version` is the sole package release version. Release changes
are recorded in `CHANGELOG.md`.

## License

Codex Coding OS is licensed under the Mozilla Public License 2.0. See
`LICENSE.md`.

Commercial use is allowed under MPL-2.0. See `COMMERCIAL-USE.md` for the
practical commercial-use boundary and third-party-license reminders.
