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
| Current state and session handoff | `project-session-continuity` | Keep coordination subordinate to the workflow manifest and controlling docs |

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
- Add a current-state file and automated session-start gate that refuse implementation when the manifest does not permit coding.
