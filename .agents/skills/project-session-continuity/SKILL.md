---
name: project-session-continuity
description: Use when starting, resuming, ending, handing off, reviewing, merging, rebasing, or deciding whether to continue a non-trivial coding session. Maintains a live coordination state, checks Git synchronization, enforces deterministic session boundaries, creates persistent handoffs, and prevents a new session from bypassing the project documentation manifest or permission-to-code gate.
---

# Project Session Continuity

Keep project state inspectable across Codex, Claude, human, and other agent sessions. This skill coordinates delivery state only. It never overrides the project brief, controlled product docs, TDD, architecture decisions, security docs, API/schema contracts, or workflow manifest.

## Install Into A Project

Copy `scripts/session_continuity.py` to `scripts/agent/session_continuity.py`.

Then run:

```text
python scripts/agent/session_continuity.py init
python scripts/agent/session_continuity.py validate
```

Use `py -3` instead of `python` on Windows when needed.

## Start Or Resume

For every new or resumed non-trivial session:

1. Run `python scripts/agent/session_continuity.py start --start-new`.
2. Stop if it reports `BLOCKED` or `INSPECTION_REQUIRED`.
3. Use `python scripts/agent/session_continuity.py start --continue-slice` only when continuing the same bounded active slice with known dirty work after inspecting the local changes.
4. Inspect incoming commits before pulling or building on them.
5. Read root and scoped agent instructions.
6. Read `docs/delivery/current-state.md`.
7. Read `docs/delivery/active-slice-manifest.json`.
8. Read the latest handoff referenced by `last_handoff`.
9. Read the workflow manifest and task-controlling sources.
10. Confirm the requested task matches `next_action`, `next_slice`, and the active-slice manifest.

The start gate must block an implementation next action unless the workflow manifest and active-slice manifest independently permit coding.

## Continue Current Session Only When

- Work remains the same bounded slice.
- The agent is responding to review findings for that slice.
- The agent is validating or fixing defects found during that slice.
- No hard new-session trigger has fired.

## New-Session Triggers

Run `python scripts/agent/session_continuity.py decide` before crossing into another slice or ending a meaningful slice.

A new session is required when:

- the live state requires it
- a merge or history rewrite changes the baseline
- two or more context-related corrections occurred
- context is stale
- work will split across agents
- incoming remote work changes the baseline

Area changes and high-risk next slices require a fresh start gate, controlling-source reread, active-slice manifest update, and explicit user approval. They do not by themselves require a new chat when those controls can be completed safely.

## Parallel Worktree Lane Gate

When a material or high-risk implementation could be split across Codex threads,
offer parallel worktree lanes only after the session-start gate passes and the
workflow manifest independently permits coding.

Use:

```text
python scripts/agent/worktree_lanes.py evaluate --task "<task>" --risk material
```

Codex may offer lane mode when the task is material, high-risk, or naturally
separable into implementation, review, test hardening, docs alignment, or security
review lanes.

Lane mode is blocked when:

- the workflow manifest or active-slice manifest does not permit coding;
- material decisions or source conflicts remain open;
- Git state is dirty, behind, or unreviewed;
- lane file ownership cannot be stated;
- lanes would overlap on files;
- controlled files would be edited without explicit approval;
- validation commands are missing;
- the user has not explicitly approved creating worktrees.

The default thread mode is manual. Codex creates worktrees and paste-ready lane
prompts, then the user opens each lane thread intentionally. Fully automated thread
creation is advanced and must show a clear risk warning first. Use it only when
trusted thread-creation tools are available and the user explicitly accepts the
risk.

Only the parent/orchestrator session may update `docs/delivery/current-state.md`,
merge lanes, or close the overall parallel run. Lane sessions must follow their
task contract, stop when the contract is insufficient, and end with a lane handoff.

## End And Handoff

When a trigger fires:

Apply the outcome-control rule from `AGENTS.md` before creating or updating coordination artifacts. Do not turn a handoff, review wait, GitHub check, current-state update, or closeout note into another support-only workflow unless it directly completes the requested outcome, unblocks it, satisfies a mandatory control, or prevents a concrete error.

1. Stop at a safe checkpoint.
2. Update `docs/delivery/current-state.md` with actual state and the exact next permitted action.
3. Run:

   ```text
   python scripts/agent/session_continuity.py handoff --topic "<topic>" --reason "<trigger>" --next "<exact next slice>" --write
   ```

4. Replace every generated `[Agent must ...]` placeholder.
5. Run `python scripts/agent/session_continuity.py validate`.
6. Run relevant project validation and `git status -sb`.
7. Give the user a paste-ready next-session prompt plus the first command to run inside that session.

The next-session prompt must include the repository path, required reading order, latest current-state path, active-slice manifest path, handoff path, workflow manifest path, the exact next permitted action, and stop conditions. It must not imply that coding is permitted unless the workflow manifest and active-slice manifest independently permit coding.

For parallel lane work, the parent session must also give each lane its paste-ready
prompt from `docs/delivery/parallel-worktrees/<run-id>/prompts/`, or create
separate Codex threads only after the user explicitly accepts fully automated
thread mode.

## Source And Gate Rules

- `docs/delivery/current-state.md` is a coordination source, not a product or technical authority.
- `docs/delivery/active-slice-manifest.json` is the current permission boundary for implementation files, forbidden actions, validation commands, review state, and stop conditions.
- Handoff notes record state. They do not approve requirements, architecture, security, or coding.
- The workflow manifest remains authoritative for phase status, open material decisions, and permission to code.
- Review state must be explicit. Use fields such as `review_required`, `review_status`, `reviewed_sha`, and `review_applies_to_active_slice`; never treat a retained marker string as review completion.
- A fresh session must continue from the first blocked or incomplete documentation phase when coding is not permitted.
- Notifications, review markers, or handoffs never grant permission to merge, deploy, or bypass validation.
- If coordination work starts generating more coordination work, report the loop and return to the requested outcome, the mandatory control, or a clear blocker.

## Completion Standard

Do not call continuity ready unless:

- current state contains every required field and section
- latest handoff exists when referenced and contains no generated placeholders
- workflow manifest path resolves when declared
- active-slice manifest path resolves when declared
- implementation next actions are blocked until both manifests permit coding
- Git state and checks are reported honestly
