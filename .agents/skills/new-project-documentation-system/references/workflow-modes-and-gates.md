# Documentation Workflow Modes And Checks

## Modes

| Mode | Use when | Completion meaning |
|---|---|---|
| Full Run | The skill is invoked for a new project or source folder without a narrower limit | Documentation phases 0 through 8 pass |
| Review Only | The user requests review or gap analysis without file changes | Findings delivered, no drafting claim |
| Single Phase | The user explicitly requests one deliverable or phase | Requested documentation completed, wider workflow remains open |
| Resume | A documentation manifest exists from an earlier run | Continue the first incomplete documentation phase |

## Documentation Status Values

Use only:

- `not_started`
- `in_progress`
- `blocked`
- `awaiting_approval`
- `approved`
- `completed`
- `explicitly_deferred`

Only one documentation phase may be `in_progress`.

These values describe document creation. They do not permit or stop coding and must not mirror campaign state.

## Checks

### A. Scope

Record mode, output location, required formats, selected skills, and the requested deliverables before inventory or drafting.

### B. Source Authority

If sources conflict on a material statement, identify the conflict and ask the user which source controls. Repetition across generated files is not independent confirmation.

### C. Material Decisions

Do not draft dependent product material while material decisions remain open. Consolidate the questions into one decision request.

### D. Controlled Documents

Treat generated product documents as drafts until approved. Do not represent draft content as accepted project truth.

### E. TDD Alignment

Do not call a TDD merged unless every competing statement was classified as keep, correct, reject, or defer. Do not complete alignment while the TDD contradicts approved product documents.

### F. Repository Documentation

Create stage-appropriate repository documentation and agent instructions. Instructions must point to stable sources, exact validation commands, and the installed campaign CLI for automated execution.

### G. Final Documentation Validation

Run the workflow-manifest and filled-artifact validators. Report any unavailable check or unresolved documentation gap.

## Approval Rules

Record document approval only from an explicit user statement, a controlling source with clear decision authority, or explicit delegated document-approval authority. Silence and model inference are not approval.

Repository summaries, current-state files, active-slice files, handoffs, review markers, branches, pull requests, and notifications are informational only. They cannot approve documents or control implementation.

## Implementation Bridge

Documentation completion does not start an implementation lifecycle.

- Manual implementation requires an explicit current user request.
- Automated implementation requires a separately admitted and user-approved campaign specification.
- Query automation through `python <installed-cli> --json status --repository-root .`.
- Admit automation through `python <installed-cli> --json admit --spec <path>`.
- Use only the campaign ID, specification digest, and next command returned by the engine.

Do not reproduce automated execution rules in documentation.
