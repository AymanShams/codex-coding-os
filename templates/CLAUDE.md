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
- Automation Coding Mode is opt-in only. Do not create automated child chats or worktrees unless the user explicitly approved the repo, run plan, thread or worktree creation, stop conditions, review expectations, and publication authority. Prefer a sequential session train: finish the current bounded task, provide one exact `Recommended Next Action`, include a paste-ready next prompt when needed, and stop.

## Current Status
{{short_status_summary}}

## Next Best Task
{{next_task}}
