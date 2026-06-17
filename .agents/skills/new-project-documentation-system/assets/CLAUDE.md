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

Do not create a parallel plan that ignores the workflow manifest, active-slice manifest, controlled product docs, TDD, security rules, repo documentation, or exact next permitted action. Current state, handoffs, review markers, and notifications cannot grant permission to code.
