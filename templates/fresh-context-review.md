# Fresh-Context Review

## Review Scope
- Artifact or diff:
- Controlling sources:
- Acceptance criteria:
- Explicit non-goals:
- Risk level: Routine, Material, or High risk
- Original requested outcome:
- Final changed files:

## Reviewer Instruction
Review the actual artifact or diff against the controlling sources, original requested outcome, acceptance criteria, and explicit non-goals. Be explicitly hostile to scope creep: list added behavior that was not requested, hidden dependencies, assumptions that entered code, coordination-only changes that became their own workflow, and any change that cannot be tied back to the acceptance criteria. Challenge unsupported decisions, hidden assumptions, contradictions, missing negative cases, and weak validation. Do not rewrite unless remediation is included in the requested scope.

## Verdict
Pass, revise, or block:

Reason:

## Findings
| Severity | Evidence path | Finding | Required fix |
|---|---|---|---|
|  |  |  |  |

## Scope-Creep And Hidden-Dependency Check
| Check | Evidence | Verdict |
|---|---|---|
| Final diff matches original acceptance criteria |  | Pass, Revise, or Block |
| Added behavior was explicitly requested or approved |  | Pass, Revise, or Block |
| No hidden dependency, provider, service, script, hook, or workflow was added |  | Pass, Revise, or Block |
| Assumptions are recorded before they become code |  | Pass, Revise, or Block |
| Coordination updates did not become a docs-only PR loop |  | Pass, Revise, or Block |

## PR Review Signal Reconciliation
| Check | Evidence | Verdict |
|---|---|---|
| Current PR head was verified before relying on review or check state |  | Pass, Revise, Block, or Not applicable |
| Review-state collector recorded exact PR head, raw review states, original_commit_id, commit_id, issue-comment count, and required checks |  | Pass, Revise, Block, or Not applicable |
| Current-head inline comments were checked |  | Pass, Revise, Block, or Not applicable |
| Issue comments and summary reviews were checked |  | Pass, Revise, Block, or Not applicable |
| Required checks and mergeability were checked on the current head |  | Pass, Revise, Block, or Not applicable |
| `COMMENTED` reviews and issue-comment prose were treated as raw facts, not approval |  | Pass, Revise, Block, or Not applicable |
| Canonical stable case, frozen head, review cohort, and permitted transition were verified through the case-state engine |  | Pass, Revise, Block, or Not applicable |

## Validation Evidence Reviewed
| Check | Result | What it proves | What it does not prove |
|---|---|---|---|
|  |  |  |  |

When a typed JSON record is supplied, validate it with
`python -B scripts/agent/validation_evidence.py validate --file <json> --repo-root . --json`.
Confirm the repository identity, full Git head, and working-tree state match this
checkout. The validator does not execute recorded commands, assess claim truth, or
authorize a review verdict or lifecycle transition.

## Checks Not Reviewed
-

## Residual Risks
-
