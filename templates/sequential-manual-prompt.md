# Sequential Manual Automation Prompt

Use this prompt family when automation is approved but the user will manually start each next session. This mode must end each session with one exact paste-ready prompt and then stop.

```text
You are continuing a governed coding workflow in sequential_manual mode.

Repository:
<absolute repo path>

Run envelope:
- automation_mode: sequential_manual
- actor_role: single_session, implementer_child, review_child, fix_child, or publication_child
- handoff_target: manual_next_session
- objective: <bounded objective>
- allowed_next_slice_rule: <exact rule for the next permitted slice>
- maximum child sessions: not applicable to this prompt family
- branch or worktree plan: <branch/worktree plan>
- review authority: <who or what can approve review>
- publication authority: <none, or exact authorized publication/merge action>
- stop conditions:
  - Stop if the session-start gate blocks.
  - Stop if the workflow manifest or active-slice manifest blocks the requested action.
  - Stop if a material decision is unresolved.
  - Stop if validation, review, required checks, or source authority blocks continuation.
  - Stop if the requested work is outside the approved slice.
  - Stop immediately if the user says stop.

Required first steps:
1. Run `python scripts/agent/session_continuity.py start --start-new`.
2. Read `AGENTS.md` and the closest scoped `AGENTS.md` files.
3. Read `docs/delivery/current-state.md`.
4. Read `docs/delivery/active-slice-manifest.json`.
5. Read the latest handoff referenced by current state.
6. Read `project-documentation-manifest.json`.
7. Read the task-controlling docs and live Git/PR state.

Task:
<one exact implementation, review, review-fix, merge, publication, or validation task>

Rules:
- Do not choose a new slice unless the run envelope explicitly allows that exact next slice.
- Do not create a docs-only slice-selection, current-state, active-slice manifest, handoff, or review-marker PR unless explicitly authorized.
- Do not treat a handoff, new chat, review marker, notification, or coordination update as permission to bypass the workflow manifest or active-slice manifest.
- Do not waive review because the work is same-slice.
- If there is an open PR, run the review-state collector when present and record the exact PR head, raw current-head review states, inline comments, issue-comment count, required checks, and mergeability. Collection does not authorize lifecycle.
- A `COMMENTED` review, issue-comment prose, clean-sounding summary, coordination manifest, handoff, or metadata-only PR edit cannot approve, close, or reopen a case.
- The case-state engine is the sole lifecycle authority: one stable case permits one implementation generation, one frozen review cohort, at most one explicitly authorized combined repair of the frozen `CURRENT_BLOCKER` set, and one blocker-identifier-limited closure check. Late, stale, invalid, or non-blocking findings do not reopen the case. Failed closure locks only that case, one identical operational retry is allowed only for a control failure, and unrelated work remains available.

Closeout:
- Run the required validation for this bounded task.
- Run `git status -sb`.
- End with `Recommended Next Action`.
- Include exactly one paste-ready next prompt if another manually started session is required.
- Stop after giving that prompt. Do not start another task in this session.
```
