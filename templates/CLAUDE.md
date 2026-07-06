# Agent Handoff Notes

This file mirrors the repository operating rules for assistants that read `CLAUDE.md`.

## Start Here
- Read `AGENTS.md`.
- Run `python scripts/agent/session_continuity.py start --start-new` when available.
- Use `python scripts/agent/session_continuity.py start --continue-slice` only when continuing the same bounded active slice with known dirty work after inspection.
- Read `docs/delivery/current-state.md`, `docs/delivery/active-slice-manifest.json`, its latest handoff, and `project-documentation-manifest.json`.
- Read the source docs in `docs/`.
- Follow the same validation and security rules as Codex.

## Project Truth
The controlled docs in `docs/` override chat memory and old assumptions.

## Boundaries
- Keep changes small.
- Avoid broad rewrites.
- Never commit real secrets.
- Ask when product behavior conflicts across docs.
- Do not let current state, a handoff, a review marker, or a notification override controlled sources or permission-to-code gates.
- Automation Coding Mode is opt-in only. Do not create automated child chats or worktrees unless the user explicitly approved the repo, run envelope, thread or worktree creation, stop conditions, review expectations, and publication authority.
- In sequential manual mode, finish the current bounded task, provide one exact `Recommended Next Action`, include a paste-ready next prompt when needed, and stop.
- In parent/orchestrator mode, child handoffs are internal transition artifacts for the parent unless a stop condition fires. The parent may inspect, assign, monitor, verify, reconcile, and report, but must not implement product code, merge, deploy, or publish directly.
- Before parent/orchestrator final closeout, run the review-state collector when present, then re-check the current PR head, review commit, current-head inline comments, issue comments, required checks, local branch state, working-tree state, stale-closeout status, publication stabilization evidence, and review-loop breaker evidence. Publication stabilization evidence records PR body head metadata, reviewed-head evidence, exact review authority count, post-review-fix reconciliation status, and typed metadata-only PR body check retrigger state. `metadata_only_check_retrigger` must be `not_retriggered` or `retriggered_required_checks_passed`. `bounded_wait_result` must be `not_required_no_retrigger` or `completed_required_checks_success`. Free-text clean phrases are not closeout evidence. If a review-fix push changes the PR head, reconcile those fields before starting another review or publication child. If `scripts/agent/session_continuity.py` exists, record that evidence in the active-slice manifest and run `python scripts/agent/session_continuity.py closeout-check`.
- If current-head inline findings conflict with a later no-major-issues summary, classify review state as ambiguous and stop.
- After two automated review-fix rounds on the same PR, or after three findings in the same validator area, stop for batch root-cause analysis and an adversarial test matrix before authorizing exactly one further automated review.
- Do not create docs-only slice-selection, current-state, manifest, or handoff PRs unless explicitly authorized.
- Unresolved material decisions are blockers, not permission for the agent to choose.

## Current Status
{{short_status_summary}}

## Next Best Task
{{next_task}}
