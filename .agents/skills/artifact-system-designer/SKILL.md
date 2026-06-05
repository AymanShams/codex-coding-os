---
name: artifact-system-designer
description: Use when the user asks to design, reorganize, clean up, structure, govern, or create the operating model for a document or artifact system. Use for documentation repositories, knowledge bases, governance libraries, strategy packs, project evidence rooms, SOP libraries, audit folders, client file systems, reusable document operating models, folder systems, naming standards, metadata schemas, artifact indexes, source-of-truth models, versioning rules, review states, approval flows, archives, lifecycle rules, routing rules, and maintenance discipline. Use when the user asks what should go in each folder, which file should be authoritative, how to prevent duplicates or stale files, how to organize project documents, how to build a controlled library, or how to make artifacts reusable and searchable. Do not use for drafting a single standalone SOP, policy, memo, PRD, board update, contract, or report unless the user also asks for the surrounding artifact system. If the primary task is drafting a controlled artifact, use ssot-drafter. If the primary task is documenting one recurring process, use process-docs or ssot-drafter. If the primary task is validation, acceptance checks, defect logs, evidence requirements, or pass/fail readiness, use artifact-validation-workflow. If the primary task is delivery sequencing, roadmap, issue tree, dependency tree, or WBS, use wbs-artifact-planner. If the primary task is a source-backed dossier for an entity, decision, risk, client, project, or product, use dossier-builder.
---

# Artifact System Designer

Use this skill to turn scattered documents and work outputs into a controlled artifact system. The goal is not a prettier folder tree. The goal is a system where every artifact has a purpose, owner, source status, review state, update path, and clear relationship to other artifacts.

## Use When

- The user asks how to organize a document repository, governance library, project folder, knowledge base, strategy pack, or evidence room.
- The user needs a source-of-truth model for artifacts, versions, drafts, reviews, approvals, and archives.
- The user asks what should go into each folder, file, template, index, or context pack.
- The user wants reusable artifact architecture before creating many documents.
- The work spans development, strategy, operations, governance, audits, training, client work, or formal company artifacts.

## Do Not Use When

- The user only needs one SOP, policy, memo, PRD, board update, or contract. Use the specialist drafting skill first.
- The user asks for a normal file cleanup without source-of-truth or lifecycle decisions.
- The user needs code architecture review. Use code or architecture skills instead.

## Related Skills

- Use `ssot-drafter` when the output is itself a controlled SOP, policy, workflow, operating model, or governance artifact.
- Use `process-docs` when documenting one recurring process.
- Use `wbs-artifact-planner` when the system needs a task tree, dependencies, and delivery plan.
- Use `artifact-validation-workflow` when artifacts already exist and need readiness gates or acceptance checks.
- Use `dossier-builder` when the central unit is a source-backed dossier for an entity, decision, client, risk, project, company, or product.
- Use `technical-docs-pack` when the artifact system is specifically a software-repo technical documentation pack with README, `/docs`, architecture, API, data, security, reliability, delivery, testing, ADRs, and PR documentation governance. For the reusable pattern, read `references/software-technical-docs-pack.md`.

## Core Pattern

Design around five layers:

1. Object layer: what real-world things the system tracks.
2. Artifact layer: which files represent or support those objects.
3. Source layer: which files are authoritative, draft, derived, archived, or evidence only.
4. View layer: which indexes, dashboards, summaries, decks, or reports are projections from the source layer.
5. Lifecycle layer: how artifacts are created, reviewed, approved, updated, archived, and retired.

Never let view artifacts become uncontrolled source-of-truth unless the user explicitly decides that.

## Minimum Context Gate

Use existing files and conversation first. Ask only for missing information that would materially change the design.

Required context:

1. Domain or project scope.
2. Primary artifact types.
3. Who creates, reviews, approves, and uses the artifacts.
4. Current pain point: duplication, missing ownership, stale files, unclear status, weak evidence, poor retrieval, or broken handoff.
5. Required outputs: folder model, naming standard, metadata schema, lifecycle, index, migration plan, or all of these.

If context is incomplete but enough exists to proceed, produce Version 1 and label assumptions.

## Workflow

1. Define the artifact system purpose in one paragraph.
2. Identify artifact classes, source-of-truth files, supporting evidence, derived views, and archives.
3. Map the folder or workspace structure.
4. Define naming, versioning, status, ownership, review, and archival rules.
5. Define metadata fields and index fields.
6. Define creation, review, approval, update, and retirement workflows.
7. Add quality gates and failure modes.
8. Produce a migration plan from current state to target state.
9. End with open decisions and the smallest next action.

## Artifact Classes

Use these default classes unless the user provides better ones:

| Class | Purpose | Source status |
|---|---|---|
| Canonical | Current approved source of truth | Authoritative |
| Working draft | Active draft not yet approved | Not authoritative |
| Evidence | Source material, data, meeting notes, screenshots, exports | Supporting |
| Derived view | Summary, dashboard, deck, brief, index, report | Projection |
| Template | Reusable structure for future artifacts | Controlled reusable asset |
| Archive | Superseded or inactive artifact | Historical |
| Decision record | Record of a decision, owner, rationale, and date | Authoritative for decision history |

## Required Output Sequence

1. Direct verdict: what system should be built or changed.
2. Assumptions and missing context.
3. Artifact classes and source-of-truth model.
4. Proposed folder or workspace structure.
5. Naming, metadata, status, and ownership rules.
6. Lifecycle workflow.
7. Quality gates and failure modes.
8. Migration plan.
9. Next actions ranked by impact.

## Folder Structure Template

````markdown
# Artifact System: [Name]

## Purpose
[What the system makes easier, safer, or more reliable.]

## Scope
Included:
- [Artifact domain]

Excluded:
- [What belongs elsewhere]

## Source-of-Truth Model
| Artifact class | Authoritative location | Owner | Review cadence | Notes |
|---|---|---|---|---|
| Canonical |  |  |  |  |
| Evidence |  |  |  |  |
| Derived view |  |  |  |  |

## Folder Structure
```text
[root]/
  00_Admin_Index_and_Manifests/
  01_Canonical/
  02_Working_Drafts/
  03_Evidence/
  04_Derived_Views/
  05_Templates/
  90_Archive/
```

## Naming Standard
Pattern:
`[Domain]_[ArtifactType]_[ShortName]_[VersionOrDate].[ext]`

Rules:
1. Use stable names for canonical artifacts.
2. Use dates for snapshots, logs, exports, and evidence.
3. Do not encode approval status only in the filename. Track status in metadata or index.

## Metadata Standard
| Field | Required | Purpose |
|---|---:|---|
| title | Yes | Human-readable artifact name |
| artifact_type | Yes | SOP, policy, report, deck, evidence, template, etc. |
| owner | Yes | Accountable role or person |
| status | Yes | Draft, For Review, Approved, Superseded, Archived |
| source_status | Yes | Authoritative, Supporting, Projection, Historical |
| last_reviewed | No | Review date |
| next_review_due | No | Maintenance trigger |
| related_artifacts | No | Links to dependencies |

## Lifecycle
1. Create draft.
2. Attach source evidence.
3. Review for scope, facts, completeness, and downstream impact.
4. Approve and move to canonical location.
5. Update index.
6. Archive superseded version.
7. Set next review date.

## Quality Gates
- No orphan artifact without owner.
- No canonical artifact without status.
- No derived view treated as source without explicit decision.
- No duplicate source-of-truth file for the same topic.
- No archive move without traceable replacement or reason.
````

## Failure Modes

- Folder design mirrors departments instead of how artifacts are used.
- Templates multiply faster than actual controlled artifacts.
- Index files become stale because no owner updates them.
- Evidence is mixed with approved artifacts, making review status unclear.
- Derived summaries overwrite source material.
- Status is hidden in filenames and becomes unreliable.
