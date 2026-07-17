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

Before parent/orchestrator final closeout, re-check the exact PR head, current-head review records, inline comments, issue comments, required checks, local branch, local HEAD, and working-tree state. GitHub collection and the continuity helper report raw facts only. `COMMENTED` reviews and issue-comment prose are never approval or closure authority.

Canonical finite case lifecycle: the internal case-state engine is the sole lifecycle authority. Each stable case permits one implementation generation, one frozen review cohort, at most one explicitly authorized combined repair of the frozen `CURRENT_BLOCKER` set, and one closure check limited to those blocker identifiers. Chat, branch, PR, counter, manifest, comment, handoff, or prose changes cannot authorize another generation. Late, stale, invalid, or non-blocking findings do not reopen the case. Failed closure locks only that case, one identical operational retry is allowed only for a control failure, and unrelated work remains available.
