# Template Ownership And Output Contracts

## Ownership Rule

Keep one authoritative owner for each detailed template. The orchestrator enforces sequence and completeness, not duplicate wording.

| Output | Template or method owner | Orchestrator responsibility |
|---|---|---|
| Project brief | This skill asset | Require it before the PRD |
| Decision register | This skill asset | Require all material decisions to be resolved |
| PRD | `create-prd` | Ensure source authority, completeness, and approval |
| App flow | `ssot-drafter` plus project workflow sources | Ensure alternate paths, failures, decisions, statuses, and handoffs |
| Tech stack | `technical-docs-pack` plus relevant platform skills | Ensure choices, rejected alternatives, migration constraints, and approvals |
| Frontend guidelines | Relevant frontend skills plus controlled product sources | Ensure routes, states, accessibility, permissions, and design constraints |
| Backend structure | `technical-docs-pack` plus architecture/security skills | Ensure modules, data, APIs, jobs, integrations, audit, and error handling |
| Security guidelines | `security-best-practices` and `security-threat-model` when applicable | Ensure sensitive data, identity, authorization, logging, secrets, and incident controls |
| Implementation plan | `wbs-artifact-planner` | Ensure dependencies, milestones, exit criteria, validation, and first vertical slice |
| TDD | `wbs-artifact-planner` plus `technical-docs-pack` | Ensure it is source-locked and aligned |
| Alignment review | This skill asset | Record keep/correct/reject/defer decisions and drift |
| Full repo docs | `technical-docs-pack/references/repo-docs-template.md` | Enforce stage fit and coverage |
| Validation report | `artifact-validation-workflow` | Require pass/fail verdict and exact blockers |
| Current state, active-slice manifest, and session handoff | `project-session-continuity` | Keep coordination subordinate to the workflow manifest, active-slice manifest, and controlling docs |

## Artifact Identity Rule

`pack.manifest.json#artifact_definitions` is the machine-readable authority for
template identity. A filename, similar purpose, or bundled location does not by
itself establish whether two files are mirrors or variants.

| Relationship | Required behavior |
|---|---|
| Canonical | One authoritative artifact owns the family contract. |
| Exact mirror | The mirror has the same owner and must remain byte-identical to the canonical artifact. |
| Intentional variant | The variant has a distinct consumer or trigger and records why it differs. |
| Derived | The artifact is a projection and records its canonical source and generation route. |

The audited template families are classified as follows:

| Family | Canonical artifact | Distributed relationship |
|---|---|---|
| Full repository documentation | `technical-docs-pack/references/repo-docs-template.md` | `templates/repo-docs-template.md` is an exact mirror. |
| Project brief | `assets/project-brief-template.md` in this skill | `templates/project-brief.md` is an exact mirror. |
| Root agent instructions | `assets/AGENTS.md` in this skill | `templates/repo-AGENTS.md` is a standalone intentional variant. |
| Scoped agent instructions | `assets/scoped-AGENTS.md` in this skill | `templates/scoped-AGENTS.md` is a standalone intentional variant. |
| Claude entrypoint | `assets/CLAUDE.md` in this skill | `templates/CLAUDE.md` is a standalone intentional variant. |
| Handoff | `assets/history-handoff-template.md` in this skill | `templates/handoff-note.md` is a session-boundary intentional variant owned by `project-session-continuity`. |

Maintenance rules:

1. Update an exact mirror in the same change as its canonical artifact.
2. Do not force intentional variants to byte equality.
3. Register a new family member before a workflow or public template consumes it.
4. Keep each registered path in `pack.manifest.json#required_files`.
5. Run `python tests/test_documentation_contracts.py` after changing a registered artifact.

## Artifact Contract Decision

| Decision | Alternatives rejected | Reason | Owner | Approver | Revisit trigger | Evidence test | Status | Authority source |
|---|---|---|---|---|---|---|---|---|
| Extend the pack manifest with typed artifact definitions and deterministic validation. | Filename convention alone. A separate registry file. Universal byte equality for all similar templates. | Existing manifests already own pack identity. Typed relationships prevent mirror drift without erasing valid variants. | Codex Coding OS maintainers | Ayman Shams | A new generator, consumer, or artifact relationship cannot be represented by the current schema. | Documentation-contract negative fixtures and full pack validation pass. | Approved | Explicit implementation and merge authorization in the current task. |

## Seven-Doc Completeness Contract

Before requesting approval, verify:

1. Each document identifies controlling sources.
2. Each material decision is either resolved or explicitly marked blocked.
3. Cross-document terms, roles, statuses, systems, and scope match.
4. No document introduces an unapproved provider, service, role, workflow, or integration.
5. App flow includes alternate paths and failure states.
6. Security controls map to the actual data and workflows.
7. Implementation plan does not imply coding approval.

## Coding OS Additions

Incorporate these durable controls from the Codex Coding OS process:

- Create a project brief before the seven-doc pack.
- Route one primary skill per phase.
- Use supporting skills only when they materially change the phase.
- Maintain a machine-readable manifest as workflow source of truth.
- Separate user-facing deliverables from maintainer/process commentary.
- Add a first vertical slice recommendation only after documentation approval.
- Add a handoff note that reports actual state, validation, known issues, and the next permitted task.
- Add a current-state file, active-slice manifest, and automated session-start gate that refuse implementation when either manifest does not permit coding.
