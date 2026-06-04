---
name: source-locked-docs-workflow
description: Use when creating or reviewing project documentation from source files, chat exports, PRDs, templates, TDD drafts, or repo docs, especially to avoid generic filler and source drift.
---

# Source-Locked Docs Workflow

Use this skill when documentation must be faithful to project sources.

## Source Classes

Classify sources before drafting:

| Class | Meaning | Treatment |
|---|---|---|
| Controlling source | User-approved project truth | Use as authority |
| Reference template | Structure only | Use for coverage |
| External draft | AI or third-party draft | Compare and correct |
| Evidence | Raw research, screenshots, exports, transcripts | Extract facts |
| Sensitive | Credentials, personal data, private business data | Do not copy to public outputs |
| Historical | Superseded names or old decisions | Use only to detect drift |

## Source Manifest

For non-trivial projects, create a manifest with:

- path
- file type
- source class
- project area
- sensitivity
- authority notes
- conflicts

## Stage Map

Use this readiness model:

| Stage | Name | Fill Standard |
|---|---|---|
| 0 | Idea and PRD | Fill from user decisions and source files |
| 1 | Product and workflows | Fill from PRD, app flow, users, and scope |
| 2 | Architecture direction | Fill from tech stack and major decisions |
| 3 | Data and API design | Fill from entities, routes, permissions, and TDD |
| 4 | Implementation | Fill only after code exists |
| 5 | Operations | Fill only after deployment and operating evidence exists |
| 6 | Maturity and scale | Fill only after usage and performance evidence exists |

## Full Repo Documentation Template

When the project needs a complete repo documentation tree, use `templates/repo-docs-template.md` from this pack as the reference structure.

Do not create every file blindly. Select the files that match the project stage, then use the stage map above to decide whether each file should be filled, partially filled, or marked not due yet.

## Not-Due Placeholder

Use this for Stage 4 to Stage 6 docs before evidence exists:

```md
# <Document Name>

## Status
Not due yet. This document depends on implementation, deployment, operating, or measured evidence that does not exist yet.

## Current Source Of Truth
- <link to controlling planning docs>

## Trigger To Fill
- Fill this document when <specific implementation or operational event occurs>.
```

Avoid `TODO`, `TBD`, and vague placeholders in filled docs.

## TDD Merge Workflow

Use this when the user has AI-generated build plans:

1. Preserve originals under `docs/_source/`.
2. Summarize useful sections.
3. Compare against controlled docs.
4. Mark each item keep, keep with correction, reject, or defer.
5. Create one merged TDD.
6. Patch controlled docs only when the TDD exposes a real gap.
7. Add an alignment review.

## Validation Gates

Before completion, check:

- required files exist
- source classes were assigned
- filled docs name controlling sources or assumptions
- stage fit is correct
- high-risk decisions match across docs
- no generic filler remains
- old project names are removed
- no secrets or sensitive data are present
- repo instruction files exist when coding will start

## Completion

Report:

- docs created or updated
- source files used
- checks passed
- checks not run
- blockers
- remaining decisions before coding
