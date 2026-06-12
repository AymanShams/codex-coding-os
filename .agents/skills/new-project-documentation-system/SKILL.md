---
name: new-project-documentation-system
description: Use when the user asks Codex to start or review a new software project, turn an idea or source folder into a complete controlled documentation system, create a project brief, PRD, app flow, tech stack, frontend, backend, security, implementation plan, TDD, repo docs, AGENTS.md, CLAUDE.md, or prepare a repo for later implementation. This is a fail-closed orchestration skill: it routes to existing specialist skills, requires a workflow manifest and explicit phase gates, stops for unresolved material decisions, and prevents coding or completion claims until the required documentation and validation phases are approved or explicitly deferred by the user.
---

# New Project Documentation System

Move from idea or source folder to a controlled, source-locked project documentation system and repo handoff. Keep this skill as the conductor. Route detailed drafting to the owning skills instead of duplicating their templates.

## Default Mode

When this skill is explicitly invoked without a narrower request, use **Full Run** mode.

Full Run means complete the workflow from source inventory through final validation. Do not silently stop after the seven source docs, a partial TDD, or a validation summary.

Use **Review Only** or **Single Phase** mode only when the user explicitly limits the scope. Record the scope limitation in the workflow manifest.

Read `references/workflow-modes-and-gates.md` before starting.

## Fail-Closed Rule

Before drafting any controlled document, create `project-documentation-manifest.json` from `assets/project-documentation-manifest.template.json`.

Keep the manifest current throughout the run. It is the workflow source of truth.

Run `scripts/validate_workflow_manifest.py <manifest-path>`:

- before drafting the PRD
- after the seven controlled source docs
- after the TDD and alignment review
- before saying repo documentation or handoff is complete
- before saying coding is the next step

If validation fails, stop at the failing gate. Do not bypass a failed gate with assumptions.

Run `scripts/validate_filled_artifacts.py <filled-artifact-paths>` before requesting approval for controlled documents, completing repo documentation, or finalizing a handoff. Do not scan blank source templates as filled artifacts.

## Material Decision Gate

The safe-assumptions exception applies only to low-risk presentation choices such as filenames, formatting, or clearly reversible organization.

Never assume or decide without the user when the answer affects:

- official product name, ownership, or source authority
- product scope, users, roles, workflows, statuses, approvals, or out-of-scope boundaries
- data sources, sensitive data, PHI, retention, exports, or source-of-truth systems
- identity, MFA, permissions, encryption, audit, privacy, compliance, or legal constraints
- integrations, AI autonomy, human approval, notifications, or external communications
- stack, hosting, deployment, environments, migration constraints, or paid services
- repo creation, external service creation, output location, required formats, or whether coding is allowed

If any of these are unknown, conflicting, or unsupported, set Phase 2 to `blocked`, list all questions together, and wait for the user before drafting the PRD. Do not treat “Version 1 with assumptions” as permission to bypass this gate.

## Capability Routing

Start with `catalogue-router`. Use one primary skill per phase and add supporting skills only when they materially change that phase.

| Phase | Primary skill | Supporting skills |
|---|---|---|
| Source extraction | `doc`, `document-skills:docx`, `document-skills:pdf`, or `document-skills:xlsx` | `evidence-checker` when authority or source quality is disputed |
| Project brief and PRD | `create-prd` | `product-strategy`, `customer-journey-map`, or `working-backwards` only when needed |
| Formal controlled artifacts | `ssot-drafter` | `humanizer` only for final prose quality |
| Repo documentation tree and detailed templates | `technical-docs-pack` | `artifact-system-designer` for wider artifact governance |
| Delivery sequencing and implementation plan | `wbs-artifact-planner` | `pre-mortem` only for material delivery risk |
| Security, privacy, and development controls | `security-best-practices` | `security-threat-model` when assets and trust boundaries are known |
| Readiness, drift, and pass/fail checks | `artifact-validation-workflow` | `deep-critic` only when the user requests hard critique |
| Session start, resume, and handoff continuity | `project-session-continuity` | use after the workflow manifest exists |
| Implementation handoff | `ai-coding-discipline` | use only after the documentation workflow passes |

Use `technical-docs-pack/references/repo-docs-template.md` for the detailed repo documentation template. Do not recreate it here.

Read `references/template-ownership-and-output-contracts.md` for the ownership and completeness rules.

## Full Run State Machine

Complete phases in order. Do not advance past a blocked or awaiting-approval phase.

| Phase | Required result | Mandatory gate |
|---|---|---|
| 0. Route and scope | Mode, company context, paths, formats, code permission, selected skills | Scope recorded in manifest |
| 1. Source inventory | Classified source manifest, authority map, sensitive-file flags, conflict list | Source authority approved when conflicts exist |
| 2. Material decisions | Consolidated decision register and all required questions | No open material decisions before PRD drafting |
| 3. Project brief and seven controlled docs | Project brief plus PRD, app flow, tech stack, frontend, backend, security, implementation plan | User approval before treating docs as controlled |
| 4. TDD and alignment | TDD created from controlled docs, external drafts reconciled when present, alignment review | TDD approved and no unresolved drift |
| 5. Repo documentation | Full template-driven repo docs appropriate to the current stage | `technical-docs-pack` coverage validated |
| 6. Agent instruction layer | Root/scoped `AGENTS.md`, `CLAUDE.md`, docs index links, current-state file, session continuity command | Required reading, source hierarchy, and start gate verified |
| 7. Handoff | Persistent history note, current-state update, exact next-chat task | Handoff matches actual state and cannot bypass the manifest |
| 8. Final validation | Pass/fail report, blockers, unavailable checks, git state when applicable | Manifest validator passes |

## Controlled Status Rules

- A generated document is a **draft** until the user approves it or explicitly delegates approval.
- A source conflict remains unresolved until the user chooses authority or the sources contain an explicit precedence rule.
- A TDD is not “merged” unless external drafts were actually compared with keep, correct, reject, and defer decisions.
- If no external TDD exists, create a source-locked TDD and call it a TDD, not a merged TDD.
- Absence of a Git repo does not automatically skip repo documentation. Ask whether to create a repo, prepare repo-ready docs outside a repo, or explicitly defer the phase.
- Do not call coding the next step unless Phase 8 passes and `coding_start` approval is recorded.
- A new chat, handoff note, or current-state file cannot grant approval. Resume from the first blocked or incomplete phase in the manifest.

## Required Outputs

For Full Run mode, produce or explicitly defer with user approval:

- `project-documentation-manifest.json`
- source inventory and authority map
- project brief
- decision register
- seven controlled source docs
- TDD
- documentation alignment review
- repo documentation tree using `technical-docs-pack`
- root and scoped agent instructions
- `docs/delivery/current-state.md` and project-local session continuity command
- handoff history note and paste-ready next-chat prompt
- final validation report

Use the orchestrator-owned assets for the project brief, decision register, alignment review, manifest, and handoff. Use specialist skills for the detailed PRD, controlled docs, repo docs, and validation report.

## Completion Standard

Do not claim the workflow is complete or recommend coding unless:

- the manifest validator passes
- all material questions are resolved
- source authority is clear
- the seven controlled docs and TDD are approved
- repo documentation and agent instructions exist
- final validation is complete
- the user approved coding to start

End with:

- current mode and manifest path
- phase status table
- created or updated paths
- specialist skills used
- validation checks passed
- checks not run and why
- blockers and next permitted action
