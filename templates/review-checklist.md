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
| Review-state collector recorded review commit, PR head, original_commit_id, commit_id, clean-summary commit, required checks, and ambiguity | {{evidence}} | Pass, Fail, or Not reviewed |
| Current-head inline comments were checked | {{evidence}} | Pass, Fail, or Not reviewed |
| Issue comments and summary reviews were checked | {{evidence}} | Pass, Fail, or Not reviewed |
| Required checks and mergeability were checked on the current head | {{evidence}} | Pass, Fail, or Not reviewed |
| Current-head inline findings do not conflict with a later no-major-issues summary | {{evidence}} | Pass, Fail, Ambiguous, or Not reviewed |
| Review-loop breaker threshold was not crossed, or batch RCA and adversarial test matrix are complete before exactly one further review | {{evidence}} | Pass, Fail, Ambiguous, or Not reviewed |

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

## Final Decision
Ship, revise, or block: {{decision}}

Reason: {{reason}}

Checks not reviewed and residual risks: {{gaps_and_risks}}
