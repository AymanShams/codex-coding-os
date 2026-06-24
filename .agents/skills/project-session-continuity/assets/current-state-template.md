---
state_version: 1
last_updated: 1970-01-01
workflow_state: documentation-intake
workflow_manifest: project-documentation-manifest.json
permission_manifest: docs/delivery/active-slice-manifest.json
active_area: documentation
active_slice: project-intake
next_area: documentation
next_slice: resolve-material-decisions
next_action: resolve_material_decisions
next_high_risk: false
next_session_required_before_next_slice: false
review_required: false
review_status: not_required
reviewed_sha: none
review_applies_to_active_slice: false
last_handoff: none
---

# Current Delivery State

## Purpose
This file records the active slice, exact next permitted action, risks, and session-boundary decision. It is not a product, architecture, API, schema, data, or security authority. Correct this file when it conflicts with controlling sources.

## Active Slice Manifest
- The current permission boundary is `docs/delivery/active-slice-manifest.json`.
- A current-state update, handoff, review marker, or new chat cannot authorize work outside that manifest.
- Coordination drift alone is not a review trigger. Review need comes from actual diff risk, controlled-source risk, or explicit user instruction.
- Same-slice status is not a review waiver.
- The first-slice authorization false-negative case proves same-slice status must never waive review for authorization, role or permission enforcement, or protected-data behavior changes. Do not reopen a PR from coordination drift alone.
- Governed repo closeout must include `Recommended Next Action` and, when review, handoff, or new-session state is active or requested, the complete paste-ready prompt or an explicit statement that no prompt is required.

## Current Verified Repository State
- Record the verified branch, local head, remote baseline, and working-tree state.

## Active Work
- Record the bounded slice currently in progress.

## Next Permitted Action
- Resolve material project decisions before drafting controlled documents or coding.

## Work Explicitly Not Started
- Implementation is not started.

## Candidate Decisions Still Not Final
- Record candidates that must not be treated as approved decisions.

## Risks And Blockers
- Record material risks, blocked checks, and unresolved conflicts.

## New Session Decision
- Continue only while work remains the same bounded slice.

## New Session Start Instructions
```text
Paste the latest handoff's next-session prompt into a new Codex chat. First run the project session-start gate. Then read current state, its latest handoff, the workflow manifest, and controlling sources. Continue only from the exact next permitted action and stop if the workflow manifest blocks it.
```

## Update Contract
Update this file when the active slice, next action, risks, blockers, review state, permission manifest, latest handoff, or session-boundary decision changes. Do not copy chat history into this file.
