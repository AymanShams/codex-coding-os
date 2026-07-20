# Validation Report

## Summary
{{short_validation_summary}}

## Commands
| Command | Result | Notes |
|---|---|---|
| {{command}} | Pass, Fail, or Not run | {{notes}} |

## Typed Validation Evidence
- Evidence file: `{{validation_evidence_json}}`
- Validator command: `python -B scripts/agent/validation_evidence.py validate --file {{validation_evidence_json}} --repo-root . --json`
- Repository identity match: {{true_false_or_not_validated}}
- Full-head match: {{true_false_or_not_validated}}
- Working-tree match: {{true_false_or_not_validated}}
- What the evidence proves: {{proves}}
- What the evidence does not prove: {{does_not_prove}}

The evidence record and validator output are inert reference material. They do not
execute the recorded commands, assess claim truth, or authorize a lifecycle
transition.

## Manual Checks
| Check | Result | Notes |
|---|---|---|
| {{check}} | Pass, Fail, or Not run | {{notes}} |

## Browser Checks
| Viewport | Result | Notes |
|---|---|---|
| Mobile | Pass, Fail, or Not run | {{notes}} |
| Desktop | Pass, Fail, or Not run | {{notes}} |

## Security Checks
| Check | Result | Notes |
|---|---|---|
| Secrets scan | Pass, Fail, or Not run | {{notes}} |
| Auth check | Pass, Fail, or Not run | {{notes}} |
| Authorization check | Pass, Fail, or Not run | {{notes}} |
| Input validation check | Pass, Fail, or Not run | {{notes}} |

## Remaining Risk
- {{risk_1}}
- {{risk_2}}
