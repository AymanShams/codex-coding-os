# TDD Merge And Source Lock

Use this reference when the user provides external TDD/build-plan drafts, AI-generated build plans, or technical implementation prompts.

## Source Priority

1. Explicit user decisions in the current task.
2. Seven controlled source docs.
3. Approved merged TDD.
4. External drafts after drift correction.
5. Reference templates for coverage only.

## Merge Workflow

1. Preserve each original TDD under `docs/_source/`.
2. Summarize each draft's useful sections.
3. Compare each draft against the controlled source docs.
4. Mark each item as:
   - keep
   - keep with correction
   - reject because it conflicts
   - defer because stage is not due
5. Create one merged TDD.
6. Patch the seven controlled source docs only when the TDD exposes a real gap.
7. Add an alignment review explaining what was kept, corrected, rejected, or deferred.

## Required TDD Sections

- Purpose and source-of-truth hierarchy.
- Stack and migration constraints.
- Repo folder structure.
- Pages, routes, and major buttons.
- Role-based flows.
- Data model and table list.
- API routes and error contract.
- Background jobs and integrations.
- Security, privacy, audit, and secrets controls.
- Environment variables with placeholders only.
- Implementation sequence.
- Acceptance criteria.
- Validation checklist.

## Source-Lock Gate

Before accepting generated docs:

- Every filled doc must name its controlling sources.
- Search for generic filler and remove it.
- Search for decisions that conflict with the seven source docs.
- Search for development-stage tools being treated as permanent commitments.
- Search for old product names.
- Search for unapproved auth, database, deployment, or AI autonomy assumptions.

If a doc cannot be filled from source, mark it not due instead of inventing details.
