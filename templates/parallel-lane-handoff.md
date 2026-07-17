# Parallel Lane Handoff

## Lane
- Run ID:
- Lane:
- Branch:
- Local worktree path: keep out of committed summaries unless explicitly needed for local-only handoff.
- Base commit:
- Handoff target: Parent by default in parent/orchestrator automation
- Parent consumes next: Yes or No

## Work Completed
-

## Changed Files
-

## Validation Evidence
| Command | Result | Notes |
|---|---|---|
|  |  |  |

- Typed evidence file: `validation-evidence.json` or Not produced
- Validator command: `python -B scripts/agent/validation_evidence.py validate --file validation-evidence.json --repo-root . --json`
- Identity match: True, False, or Not validated
- Full-head match: True, False, or Not validated
- Working-tree match: True, False, or Not validated
- What the evidence proves:
- What the evidence does not prove:

Typed validation evidence is a reference-only record. The validator does not run
the recorded commands, assess whether their claims are true, or authorize review,
repair, closure, merge, publication, or any other lifecycle transition.

## Checks Not Run
-

## Source Documents Read Or Used
-

## Review Packet
- Diff:
- Controlling sources:
- Acceptance criteria:
- Validation output:
- Non-goals:
- Scope creep and hidden dependencies:
- Assumptions that entered code:
- Local runtime files: `.codex/parallel-worktrees/<run-id>/`

## Blockers Or Risks
-

## Merge Readiness
- Ready / Blocked / Needs parent decision

## Stop Conditions Hit
-

## Next Action For Parent Orchestrator
-
