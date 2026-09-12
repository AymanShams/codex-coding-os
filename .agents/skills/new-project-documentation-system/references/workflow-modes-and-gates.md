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

Resolve conflicting statements through established source precedence. Ask the user only when the remaining conflict needs a founder decision under the skill's Material Decisions boundary. Repetition across generated files is not independent confirmation.

### C. Material Decisions

Apply the skill's Material Decisions boundary. Reuse approved decisions, record source-derived technical choices, and consolidate unresolved business questions into one decision request. Pause only drafting that depends on an unresolved choice.

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
- Use the installed CLI and engine receipt through `codex-coding-os-master`.

Do not reproduce automated execution rules in documentation.
