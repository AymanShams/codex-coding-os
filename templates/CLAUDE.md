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
- Before parent/orchestrator final closeout, re-check the exact PR head, current-head review records, inline comments, issue comments, required checks, local branch, local HEAD, and working-tree state. GitHub collection and the continuity helper report raw facts only. `COMMENTED` reviews and issue-comment prose are never approval or closure authority.
- Canonical finite case lifecycle: the internal case-state engine is the sole lifecycle authority. Each stable case permits one implementation generation, one frozen review cohort, at most one explicitly authorized combined repair of the frozen `CURRENT_BLOCKER` set, and one closure check limited to those blocker identifiers. Chat, branch, PR, counter, manifest, comment, handoff, or prose changes cannot authorize another generation. Late, stale, invalid, or non-blocking findings do not reopen the case. Failed closure locks only that case, one identical operational retry is allowed only for a control failure, and unrelated work remains available.
- Do not create docs-only slice-selection, current-state, manifest, or handoff PRs unless explicitly authorized.
- Unresolved material decisions are blockers, not permission for the agent to choose.

## Current Status
{{short_status_summary}}

## Next Best Task
{{next_task}}
