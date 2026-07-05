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
- If there is an open PR, verify the current PR head, current-head inline comments, issue comments, required checks, and mergeability before recommending merge or publication.
- After any review-fix push, reconcile PR body head metadata, reviewed-head evidence, exact review authority count, and required checks before recommending another review or publication step.
- If a metadata-only PR body edit retriggers a required check, bounded-wait only while code head, PR body head, reviewed-head evidence, and local HEAD remain equal. Stop if the check stays pending past the bound or any head, review, or check signal changes.
- If current-head inline findings conflict with a later no-major-issues summary, classify review state as ambiguous and stop.

Closeout:
- Run the required validation for this bounded task.
- Run `git status -sb`.
- End with `Recommended Next Action`.
- Include exactly one paste-ready next prompt if another manually started session is required.
- Stop after giving that prompt. Do not start another task in this session.
```
