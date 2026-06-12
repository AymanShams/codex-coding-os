# Review Checklist

## Assurance Level
- Routine, Material, or High risk:
- Same-session or fresh-context review:
- Independent expertise required:

## Product Fit
- The change matches the PRD.
- The user flow still matches the app-flow doc.
- No hidden feature was added without approval.

## Code Quality
- The change is smaller than a rewrite.
- Existing helpers and patterns were reused.
- New abstractions remove real complexity.
- Error handling is explicit.
- Naming is clear.

## Security
- Protected actions are checked on the server.
- Inputs are validated.
- Secrets are not committed.
- Logs avoid secrets and sensitive user content.
- Dependency changes are justified.

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
