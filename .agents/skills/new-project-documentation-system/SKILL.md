---
name: new-project-documentation-system
description: Use when the user asks Codex to start or review a software project, turn an idea or source folder into a complete stable documentation system, create a project brief, PRD, app flow, tech stack, frontend, backend, implementation plan, TDD, repo docs, AGENTS.md, CLAUDE.md, or prepare a repo for implementation.
---

# New Project Documentation System

Turn an idea or source set into stable product and engineering documentation. This skill owns documentation creation and validation only. It never owns coding, review, publication, cancellation, or campaign status.

When `codex-coding-os-master` is available for a complete software delivery request, use this skill as its documentation owner. Route detailed drafting to the narrowest specialist skill instead of duplicating specialist templates.

## Modes

- **Full Run**: complete source inventory through final documentation validation.
- **Review Only**: inspect and report gaps without editing.
- **Single Phase**: create or update only the deliverable explicitly requested.
- **Resume**: continue the first incomplete documentation phase recorded in the project manifest.

An explicit invocation without a narrower limit defaults to Full Run. Read `references/workflow-modes-and-gates.md` before starting.

## Documentation Manifest

Before drafting controlled documents, create `project-documentation-manifest.json` from `assets/project-documentation-manifest.template.json`.

The manifest is a stable documentation ledger. It records sources, decisions, approvals of documents, artifact lineage, and documentation-phase evidence. It must declare `execution_authority: false`.

The manifest must never contain or decide campaign state, case identifiers, actor identity, leases, current heads, review status, repair counts, closure status, stop state, publication authority, or the next executable action. Repository current-state files, active-slice files, and work summaries are optional informational artifacts only.

Version 1.1 pre-registers conditional content-guidelines, public-search, and module-contract artifacts. Record complete typed trigger evidence before repository documentation advances. Keep false-trigger outputs absent. Generate and validate true-trigger outputs at the documented phases.

Run `scripts/validate_workflow_manifest.py <manifest-path>`:

- before drafting the PRD
- after the controlled source documents
- after TDD alignment
- before declaring repository documentation complete
- before final documentation completion

The validator checks the documentation contract at invocation time. It does not authorize coding or prove campaign status.

Run `scripts/validate_filled_artifacts.py <filled-artifact-paths>` before requesting document approval or completing the documentation workflow. Do not scan blank templates as filled artifacts.

## Material Decisions

Assume only reversible presentation details such as filenames, formatting, and document organization.

Ask the user before choosing a material product, business, workflow, architecture, data, integration, hosting, repository, external-service, output-format, or delivery decision. If controlling sources conflict, identify the conflict and wait for the user’s decision before drafting dependent material.

## Capability Routing

Start with `catalogue-router`. Use one primary skill per phase and add support only when it materially changes that phase.

| Phase | Primary skill | Optional support |
|---|---|---|
| Source extraction | `doc` or the matching document skill | `evidence-checker` for disputed authority |
| Project brief and PRD | `create-prd` | `product-strategy`, `customer-journey-map`, or `working-backwards` |
| Formal product documents | `ssot-drafter` | `humanizer` for final prose quality |
| Repository documentation | `technical-docs-pack` | `artifact-system-designer` for wider artifact systems |
| Implementation plan | `wbs-artifact-planner` | `pre-mortem` for material delivery risk |
| Documentation validation | `artifact-validation-workflow` | `deep-critic` only for an explicitly requested hard critique |
| Manual implementation preparation | `ai-coding-discipline` | only after documentation approval |

Use `technical-docs-pack/references/repo-docs-template.md` for detailed repository documentation. Read `references/template-ownership-and-output-contracts.md` for template ownership and completeness rules.

## Documentation Workflow

Complete documentation phases in order. A blocked document phase prevents dependent drafting, but it does not become repository execution authority.

| Phase | Required result | Documentation check |
|---|---|---|
| 0. Route and scope | Mode, company context, paths, formats, selected skills | Scope recorded |
| 1. Source inventory | Classified sources, authority map, conflicts | Conflicts identified |
| 2. Material decisions | Decision register and questions | Dependent decisions resolved |
| 3. Controlled documents | Project brief and requested product document set | User approval recorded |
| 4. TDD and alignment | Source-locked TDD and alignment review | No unresolved document drift |
| 5. Repository documentation | Stage-appropriate repository documentation | Template coverage validated |
| 6. Agent instructions | Root and scoped instructions plus documentation index | Stable sources and validation commands are clear |
| 7. Work summary | Informational summary of created files, decisions, and validation | Contains no execution authority |
| 8. Final validation | Pass/fail report and exact unresolved documentation gaps | Validators pass |

Generated documents remain drafts until the user approves them or explicitly delegates document approval. Do not call an external TDD merged unless each competing statement was classified as keep, correct, reject, or defer.

## Implementation Boundary

Documentation completion and implementation execution are separate outcomes.

For manual work, coding begins only from an explicit current user request and follows the repository’s stable sources and normal validation.

For automated work, use the canonical installed CLI at `%USERPROFILE%\.codex\coding-os\scripts\agent\campaign_engine\cli.py`:

```text
python <installed-cli> --json doctor
python <installed-cli> --json admit --spec <path>
python <installed-cli> --json approve --campaign-id <id> --specification-digest <digest>
python <installed-cli> --json run --campaign-id <id>
python <installed-cli> --json status --repository-root .
python <installed-cli> --json cancel --campaign-id <id>
```

Run `approve` only after the user approves the exact specification digest. Follow the engine receipt for subsequent actions. Never infer automated authority from a project manifest, work summary, branch, pull request, chat, or Git-tracked delivery file.

## Required Full-Run Outputs

- `project-documentation-manifest.json`
- source inventory and authority map
- project brief
- decision register
- requested controlled product documents
- TDD and alignment review
- stage-appropriate repository documentation
- root and scoped agent instructions
- informational work summary
- final documentation validation report

## Completion Standard

Documentation is complete only when:

- the documentation manifest validator passes
- material questions and source conflicts affecting the documents are resolved
- controlled documents and TDD are approved
- repository documentation and agent instructions exist
- final documentation validation is complete

Report the mode, manifest path, documentation phase status, paths created or updated, skills used, checks passed, checks not run, and unresolved documentation gaps. Report implementation and campaign status separately using exact engine or product evidence.
