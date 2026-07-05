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
- Automation Coding Mode is opt-in only. Do not create automated child chats or worktrees unless the user explicitly approved the repo, run plan, thread or worktree creation, review expectations, stop conditions, and publication authority. Prefer a sequential session train when the work is linear: finish the current bounded task, provide one exact `Recommended Next Action`, include a paste-ready next prompt when needed, and stop.
- Before parent/orchestrator final closeout, re-check the current PR head, current-head inline comments, issue comments, required checks, local branch state, working-tree state, stale-closeout status, and publication stabilization evidence. Publication stabilization evidence records PR body head metadata, reviewed-head evidence, exact review authority count, post-review-fix reconciliation status, and metadata-only PR body check retrigger state. If a review-fix push changes the PR head, reconcile those fields before starting another review or publication child. If a metadata-only PR body edit retriggers a required check, bounded-poll only while code head, PR body head, reviewed-head evidence, and local HEAD remain equal. If `scripts/agent/session_continuity.py` exists, record that evidence in the active-slice manifest and run `python scripts/agent/session_continuity.py closeout-check`.
- If current-head inline findings conflict with a later no-major-issues summary, classify review state as ambiguous and stop.
- Do not commit secrets, PHI, pilot records, medical files, generated credentials, or local tool metadata.
- Do not treat development-stage tools as permanent architecture unless the docs say so.
- Update documentation in the same change when API, database, config, security, workflow, or deployment behavior changes.
- Run available validation checks before reporting completion.
