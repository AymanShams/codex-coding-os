# Project Agent Instructions

## Required Reading Order

1. Read this file.
2. Read `CLAUDE.md` if using Claude Code.
3. Read `project-documentation-manifest.json`.
4. Run the workflow manifest validator.
5. Run `python scripts/agent/session_continuity.py start --start-new` when available.
6. Read `docs/delivery/current-state.md`.
7. Read `docs/delivery/active-slice-manifest.json`.
8. Read `docs/index.md`.
9. Read the latest handoff referenced by current state.
10. Read the controlled TDD before coding.

## Source Of Truth

Use this order:

1. Explicit user decisions in the current task.
2. Controlled product documentation.
3. Controlled TDD.
4. Repo documentation.
5. Source exports under `docs/_source/`.
6. Older chats or external drafts only as historical context.

## Rules

- Do not change architecture without updating the controlling docs.
- Do not start coding unless the workflow manifest and active-slice manifest both permit coding.
- Treat current state and handoffs as coordination records only. They cannot override controlled docs, the workflow manifest, or the active-slice manifest.
- Treat review markers and notifications as evidence to inspect, not permission to merge, deploy, or bypass validation.
- Coordination drift alone is not a review trigger. Current-state drift, manifest drift, review-field drift, handoff drift, branch drift, PR-open state, CI-wait state, or local dirty state may require inspection or reconciliation, but review need comes from actual diff risk, controlled-source risk, or explicit user instruction.
- Same-slice status is not a review waiver.
- Parent-orchestrator mode and automatic session, review, and review-fix trains are disabled. A human must deliberately start each bounded session, and no session may start or authorize another.
- Record one stable case ID before review. Use one independent review, one human-authorized combined repair for current blockers, and one final blocker-closure check.
- If current-head inline findings conflict with a later no-major-issues summary, classify review state as ambiguous and stop.
- Any remaining or new blocker, failed validation, expanded repair scope, or redesign requirement permanently marks the case `RED_LOCKED`. Root-cause analysis and adversarial tests cannot authorize another review.
- Do not commit secrets, PHI, pilot records, medical files, generated credentials, or local tool metadata.
- Do not treat development-stage tools as permanent architecture unless the docs say so.
- Update documentation in the same change when API, database, config, security, workflow, or deployment behavior changes.
- Run available validation checks before reporting completion.
