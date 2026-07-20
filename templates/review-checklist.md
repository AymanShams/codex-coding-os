# Review Checklist

## Assurance Level
- Routine, Material, or High risk:
- Same-session or fresh-context review:
- Independent expertise required:

## Product Fit
- The change matches the PRD.
- The user flow still matches the app-flow doc.
- No hidden feature was added without approval.
- The final diff maps back to the original requested outcome and acceptance criteria.
- Explicit non-goals stayed out of the diff.
- Coordination updates did not become a docs-only slice-selection, current-state, manifest, or handoff PR unless explicitly authorized.

## Code Quality
- The change is smaller than a rewrite.
- Existing helpers and patterns were reused.
- New abstractions remove real complexity.
- Error handling is explicit.
- Naming is clear.
- Material assumptions are recorded in the decision record before they affect code.

## Security
- Protected actions are checked on the server.
- Inputs are validated.
- Secrets are not committed.
- Logs avoid secrets and sensitive user content.
- Dependency changes are justified.

## Scope Creep And Dependency Drift
| Check | Evidence | Result |
|---|---|---|
| Added behavior was requested or explicitly approved | {{evidence}} | Pass, Fail, or Not reviewed |
| Hidden dependency, provider, hook, service, or workflow was not added | {{evidence}} | Pass, Fail, or Not reviewed |
| Unresolved decisions did not enter implementation | {{evidence}} | Pass, Fail, or Not reviewed |
| Parent/orchestrator did not implement product code | {{evidence}} | Pass, Fail, or Not reviewed |

## PR Review Signal Reconciliation
| Check | Evidence | Result |
|---|---|---|
| Current PR head was verified before relying on review or check state | {{evidence}} | Pass, Fail, or Not reviewed |
| Review-state collector recorded exact PR head, raw review states, original_commit_id, commit_id, issue-comment count, and required checks | {{evidence}} | Pass, Fail, or Not reviewed |
| Current-head inline comments were checked | {{evidence}} | Pass, Fail, or Not reviewed |
| Issue comments and summary reviews were checked | {{evidence}} | Pass, Fail, or Not reviewed |
| Required checks and mergeability were checked on the current head | {{evidence}} | Pass, Fail, or Not reviewed |
| `COMMENTED` reviews and issue-comment prose were treated as raw facts, not approval | {{evidence}} | Pass, Fail, or Not reviewed |
| Canonical stable case, frozen head, review cohort, and permitted transition were verified through the case-state engine | {{evidence}} | Pass, Fail, or Not reviewed |

## Frontend
- Mobile and desktop layouts were checked.
- Loading, empty, and error states exist where needed.
- Text does not overlap.
- Browser console has no relevant errors.

## Validation
| Check | Command or method | Result |
|---|---|---|
| Install | {{command}} | Pass, Fail, or Not run |
| Lint | {{command}} | Pass, Fail, or Not run |
| Tests | {{command}} | Pass, Fail, or Not run |
| Build | {{command}} | Pass, Fail, or Not run |
| Manual QA | {{method}} | Pass, Fail, or Not run |

## Typed Validation Evidence
| Check | Evidence | Result |
|---|---|---|
| Evidence conforms to `validation-evidence.schema.json` | {{validator_output}} | Pass, Fail, or Not reviewed |
| Repository identity, full head, and working-tree state match the reviewed checkout | {{validator_output}} | Pass, Fail, or Not reviewed |
| Every record states what it proves and what it does not prove | {{evidence_path}} | Pass, Fail, or Not reviewed |
| No credential or private machine path appears | {{evidence_path}} | Pass, Fail, or Not reviewed |
| Evidence was treated as reference-only and not as lifecycle authority | {{case_state_evidence}} | Pass, Fail, or Not reviewed |

## Final Decision
Ship, revise, or block: {{decision}}

Reason: {{reason}}

Checks not reviewed and residual risks: {{gaps_and_risks}}
