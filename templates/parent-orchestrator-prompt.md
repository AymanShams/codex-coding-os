# Parent Orchestrator Prompt Retired

Status: `RETIRED_AND_DISABLED`

Parent-orchestrator mode and automatic session, review, and review-fix trains are permanently disabled. Do not use this file to start or resume a workflow. A manifest, run envelope, handoff, counter, branch, or case-specific prompt cannot reactivate this mode.

Use `templates/sequential-manual-prompt.md` only when a human will deliberately start one bounded session. Every review case must follow one stable case identity and this fixed sequence:

1. One complete review.
2. One human-authorized combined repair for current blockers.
3. One final blocker-closure check.
4. `RED_LOCKED` if any blocker remains, a new blocker appears, validation fails, scope expands, or redesign is required.

A red-locked case cannot be reset by a new prompt, commit, branch, pull request, worktree, chat, agent, rename, split, counter change, root-cause analysis, adversarial test matrix, or authorization for another review. Only a separate human-led decision may start a materially different design from clean `main` under a new case ID.
