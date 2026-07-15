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
- Parent-orchestrator mode and automatic session, review, and review-fix trains are disabled. A human may start one bounded session, but no session may start or authorize the next one.
- Record one stable case ID before review. Missing or conflicting identity fails closed to human review, and renaming, branching, opening another pull request, changing chats, or changing counters cannot reset it.
- Use one independent review, one human-authorized combined repair for current blockers, and one final blocker-closure check. Classify findings as `current_blocker`, `non_blocking`, `invalid_or_stale`, or `redesign_required`.
- Any remaining or new blocker, failed validation, expanded repair scope, or redesign requirement permanently marks the case `RED_LOCKED`. Root-cause analysis and adversarial tests do not authorize another review.
- Only a separate human-led decision may start a materially different design from clean `main` under a new case ID.
- Finish the current bounded task, provide one exact `Recommended Next Action`, include a paste-ready prompt when a later human-started session is needed, and stop.
- Do not create docs-only slice-selection, current-state, manifest, or handoff PRs unless explicitly authorized.
- Unresolved material decisions are blockers, not permission for the agent to choose.

## Current Status
{{short_status_summary}}

## Next Best Task
{{next_task}}
