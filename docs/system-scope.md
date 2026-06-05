# System scope

`codex-coding-os-starter` is the package name. The installed system is the Codex Coding OS.

## Included in full

This repo bundles coding-related local skills in full under `.agents/skills/`.

The included set covers:

- project kickoff and PRD creation
- technical documentation and repo instructions
- implementation planning and work breakdown
- AI coding discipline
- architecture review and refactoring
- React, React Native, frontend, and composition guidance
- CLI creation
- browser and UI QA
- source-backed review and critique
- hard planning interviews
- design artifacts and UI concepts
- public docs and README prose polish
- security best practices, threat modeling, ownership risk, and defensive checklists
- incident response, RCA, recurring defects, and improvement control
- numeric logic and KPI review
- DOCX/PDF source intake
- capability mining from prior AI chats
- external skill overlay handling

## Referenced through Codex

Codex-managed skills, plugins, and connectors stay managed by Codex. They are listed in:

- `codex-capabilities/default-skills-reference.md`
- `codex-capabilities/plugins.manifest.json`
- `docs/codex-plugins-mcps-hooks.md`

## Referenced as tools or MCPs

Local tools, command-line helpers, and MCP servers are listed in:

- `codex-capabilities/tools.manifest.json`
- `docs/codex-plugins-mcps-hooks.md`

They are not silently installed by this repo because tool and MCP installation can require system permissions, network access, API keys, auth, or trust prompts.

## Referenced as external repositories

External skill and tool sources are listed in:

- `THIRD_PARTY_SKILLS.md`
- `external-skills/manifest.json`
- `docs/external-skills-installation.md`

Most external repositories are reference-only. The Karpathy-inspired skill source has an optional install path with an overlay.

## Deliberately excluded

The following local skills are not bundled because they are outside the coding OS scope requested for this repo:

- `startup-context`
- `competitor-analysis`
- `market-sizing`
- `pricing-strategy`
- `gtm-strategy`
- `board-update`
- `charlie`
- `dossier-builder`
- `growth-seo-ads-auditor`
- `healpath-knowledge-layer`
- `novel-writing`
- `stakeholder-map`

`contract-review` is not bundled as a coding skill. Use qualified legal review for binding agreements, license interpretation, or commercial terms.
