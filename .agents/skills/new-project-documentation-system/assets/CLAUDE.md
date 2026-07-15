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

Parent-orchestrator mode and automatic session, review, and review-fix trains are disabled. A human must deliberately start each bounded session, and no session may start or authorize another. Record one stable case ID before review. Use one independent review, one human-authorized combined repair for current blockers, and one final blocker-closure check. Any remaining or new blocker, failed validation, expanded repair scope, or redesign requirement permanently marks the case `RED_LOCKED`. Root-cause analysis and adversarial tests cannot authorize another review. Finish the current bounded task, provide one exact `Recommended Next Action`, include a paste-ready next prompt when a later human-started session is needed, and stop.
