# Worktree Task Contract

## Lane Identity
- Run ID:
- Lane:
- Branch:
- Base commit:
- Local worktree path: keep in local runtime files only; do not copy into commit-safe summaries.
- Automation mode:
- Actor role:
- Handoff target:
- Integration owner: parent/orchestrator session
- Current-state owner: parent/orchestrator session
- Risk level: Routine / Material / High
- Thread mode: Manual / Fully automated

## Objective


## Non-Goals
- Do not edit files outside the allowed file list.
- Do not change controlled source truth unless explicitly allowed below.
- Do not merge this lane.
- Do not update `docs/delivery/current-state.md`.

## Controlling Sources
- `project-documentation-manifest.json`
- `docs/delivery/current-state.md`
- PRD:
- TDD:
- ADRs:
- AGENTS:
- Other:

## Allowed Files
-

## Forbidden Files
-

## Controlled Files Explicitly Allowed
- None by default.

## Decision Record For This Lane
| Decision | Alternatives rejected | Reason | Owner | Approver | Revisit trigger | Evidence test | Status | Authority source |
|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  | Proposed, Approved, Rejected, Deferred, Needs human, or Superseded |  |

## Validation Commands
-

## Typed Validation Evidence
- Evidence file: `validation-evidence.json` or another contract-approved path
- Validate with: `python -B scripts/agent/validation_evidence.py validate --file <json> --repo-root . --json`
- Use a full lowercase 40-character Git commit hash.
- State both what each result proves and what it does not prove.
- Do not include credentials or private machine paths.
- Treat the evidence and validator output as reference-only. They do not authorize
  a case transition, review result, repair, merge, publication, or deployment.

## Review Requirement
- Same-session / fresh-context / independent / human

## Merge Conditions
-

## Stop Conditions
- Workflow manifest does not permit this task.
- Required controlling source is missing.
- Sources conflict.
- The lane needs a forbidden or controlled file not explicitly allowed.
- A material product, architecture, security, or data decision is discovered.
- A handoff target or automation authority is unclear.
- Validation cannot run or fails for reasons outside this lane's contract.

## Required Handoff
- Use `templates/parallel-lane-handoff.md`.
- Include changed files, validation evidence, checks not run, blockers, and review packet.
- Include the typed evidence path and validator output when typed evidence is produced.
- Keep absolute machine paths in local runtime notes only.
