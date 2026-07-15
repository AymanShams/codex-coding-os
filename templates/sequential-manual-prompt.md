# Sequential Manual Session Prompt

Use this prompt only when a human will deliberately start one bounded session. The session must stop after its exact task. It cannot start, authorize, or chain another session.

```text
You are continuing one human-directed governed coding task.

Repository:
<absolute repo path>

Stable case identity:
- case_id: <immutable-repository-id:pr:number:problem-family, or a human-recorded UUID before a PR exists>
- case_status: <unreviewed, reviewed, repair_authorized, final_check, RED_LOCKED>

Task boundary:
- objective: <one bounded objective>
- exact action: <implementation, initial review, combined repair, final blocker-closure check, merge, publication, or validation>
- branch or worktree: <exact branch or worktree>
- allowed files: <exact files or manifest authority>
- publication authority: <none, or exact authorized action>
- stop conditions:
  - Stop if the session-start gate blocks.
  - Stop if the workflow manifest or active-slice manifest blocks the action.
  - Stop if a material decision is unresolved.
  - Stop if validation, source authority, or scope blocks continuation.
  - Stop if the task would exceed the fixed review sequence.
  - Stop immediately if the user says stop.

Required first steps:
1. Run the repository session-start gate when available.
2. Read AGENTS.md and the closest scoped AGENTS.md files.
3. Read current state, active-slice manifest, latest handoff, workflow manifest, and task-controlling sources.
4. Inspect live Git and pull-request state when relevant.
5. Confirm the stable case ID has not changed or been replaced.

Permanent review sequence:
1. Run deterministic checks.
2. Run one independent review that collects the whole finding set and classifies every finding as current_blocker, non_blocking, invalid_or_stale, or redesign_required.
3. After explicit human authorization, allow one combined repair for all current_blocker findings only.
4. Run one final blocker-closure check. It verifies only closure of the authorized blockers and whether the repair created a new blocker. It is not an open-ended review.
5. If a blocker remains, a new blocker appears, validation fails, repair exceeds scope, or redesign is required, set case_status to RED_LOCKED and stop all automated work on the case.

Rules:
- A new prompt, commit, branch, pull request, worktree, chat, agent, rename, split, close/reopen action, counter change, root-cause analysis, or adversarial test matrix cannot reset the stable case ID or red lock.
- Do not create a docs-only coordination pull request unless explicitly authorized.
- Do not treat coordination state as permission to bypass controlled sources.
- Do not waive review because work is inside the same slice.
- Return all decisions to the human. Do not start another session or task.

Closeout:
- Run the required validation for this bounded task.
- Run git status -sb.
- End with Recommended Next Action.
- If a later human-started session is needed, include exactly one paste-ready prompt.
- Stop after giving that prompt.
```
