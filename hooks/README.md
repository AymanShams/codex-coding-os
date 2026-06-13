# Hooks

This pack documents hook candidates but does not enable hooks during installation.

Recommended hook candidates:

1. Pack validation with `scripts/validate-pack.ps1` before release.
2. Release-safety scan with `scripts/release-safety-scan.ps1` before public publishing.
3. Secret-pattern scan before commit.
4. Frontend QA after UI changes.
5. External skill overlay reapply after optional external install.
6. Parallel worktree lane validation with `hooks/worktree-lane-pre-commit.py`
   and `hooks/worktree-lane-pre-push.py` for projects that enable lane mode.

Before enabling any hook, verify current Codex hook syntax and trust behavior in official Codex docs. For command approval policy, review `.codex/rules/default.rules` and `docs/codex-rules.md`.

The worktree lane hooks are optional Git hook wrappers. They call
`python scripts/agent/worktree_lanes.py validate --current` and fail closed when
an active lane edits files outside its contract, touches controlled files without
permission, or lacks required lane state.

If an active lane marker exists but `scripts/agent/worktree_lanes.py` is missing,
the hooks fail closed. Passing this hook means the lane contract is valid; it does
not replace the lane's declared tests, build, lint, or review commands.
