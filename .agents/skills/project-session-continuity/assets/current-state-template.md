---
state_version: 1
last_updated: 1970-01-01
workflow_state: documentation-intake
workflow_manifest: project-documentation-manifest.json
active_area: documentation
active_slice: project-intake
next_area: documentation
next_slice: resolve-material-decisions
next_action: resolve_material_decisions
next_high_risk: false
next_session_required_before_next_slice: false
last_handoff: none
---

# Current Delivery State

## Purpose
This file records the active slice, exact next permitted action, risks, and session-boundary decision. It is not a product, architecture, API, schema, data, or security authority. Correct this file when it conflicts with controlling sources.

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
Run the project session-start gate. Read current state, its latest handoff, the workflow manifest, and controlling sources. Continue only from the exact next permitted action.
```

## Update Contract
Update this file when the active slice, next action, risks, blockers, review state, latest handoff, or session-boundary decision changes. Do not copy chat history into this file.
