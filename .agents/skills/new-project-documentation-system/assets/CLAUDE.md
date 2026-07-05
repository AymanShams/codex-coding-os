# Claude Code Entry Point

Follow `AGENTS.md` first. This repo uses the same source-of-truth and validation rules for Claude Code, Codex, and human contributors.

Before implementation:

1. Read `AGENTS.md`.
2. Read `project-documentation-manifest.json`.
3. Run the workflow manifest validator.
4. Run `python scripts/agent/session_continuity.py start --start-new` when available.
5. Read `docs/delivery/current-state.md`.
6. Read `docs/delivery/active-slice-manifest.json`.
7. Read `docs/index.md`.
8. Read the latest handoff referenced by current state.
8. Read the controlled TDD.
9. Run `git status -sb`.

Do not create a parallel plan that ignores the workflow manifest, active-slice manifest, controlled product docs, TDD, security rules, repo documentation, or exact next permitted action. Current state, handoffs, review markers, and notifications cannot grant permission to code. Coordination drift alone is not a review trigger. Same-slice status is not a review waiver.

Automation Coding Mode is opt-in only. Do not create automated child chats or worktrees unless the user explicitly approved the repo, run plan, thread or worktree creation, review expectations, stop conditions, and publication authority. Prefer a sequential session train when the work is linear: finish the current bounded task, provide one exact `Recommended Next Action`, include a paste-ready next prompt when needed, and stop.

Before parent/orchestrator final closeout, re-check the current PR head, current-head inline comments, issue comments, required checks, local branch state, working-tree state, stale-closeout status, and publication stabilization evidence. Publication stabilization evidence records PR body head metadata, reviewed-head evidence, exact review authority count, post-review-fix reconciliation status, and metadata-only PR body check retrigger state. If a review-fix push changes the PR head, reconcile those fields before starting another review or publication child. If a metadata-only PR body edit retriggers a required check, bounded-poll only while code head, PR body head, reviewed-head evidence, and local HEAD remain equal. If the check stays pending past the bound or any head, review, or check signal changes, stop. If `scripts/agent/session_continuity.py` exists, record that evidence in the active-slice manifest and run `python scripts/agent/session_continuity.py closeout-check`. If current-head inline findings conflict with a later no-major-issues summary, classify review state as ambiguous and stop.
