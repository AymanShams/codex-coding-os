# Review Doctrine

Review is an assurance control. It must challenge the work against sources, intended behavior, and material risks rather than repeat the authoring context.

## Review Levels

| Level | Use when | Required assurance |
|---|---|---|
| Routine | Small, reversible, low-risk change | Same-session diff review plus deterministic checks |
| Material | Shared behavior, architecture, data contracts, broad refactors, or meaningful product decisions | Fresh-context review against controlling sources and acceptance criteria |
| High risk | Identity, authorization, sensitive data, payments, migrations, production infrastructure, deployment, legal or regulated workflows | Independent review with stronger expertise diversity, plus deterministic and human approval gates where required |

Another agent provides context separation, not guaranteed independent expertise. For high-risk work, prefer a different model or provider and a qualified human or domain reviewer when practical.

## Reviewer Inputs

Give the reviewer only what is needed:

- controlling sources and source hierarchy
- artifact or diff under review
- acceptance criteria
- risk-specific review lenses
- validation evidence
- explicit non-goals

Do not give the reviewer a persuasive summary that pre-decides the verdict.

## Reviewer Duties

The reviewer must:

1. Check source faithfulness and unsupported decisions.
2. Challenge assumptions, evidence quality, and hidden scope.
3. Inspect the actual artifact or diff.
4. Check intended behavior, negative cases, and failure modes.
5. Distinguish blocking defects from optional improvements.
6. State what was not reviewed or could not be verified.
7. Avoid rewriting unless the review request includes remediation.

## Required Output

- verdict: pass, revise, or block
- findings ordered by severity
- evidence path for each material finding
- required fix
- optional improvement
- checks reviewed and checks missing
- residual risks

## Independence Rules

- The implementing agent remains accountable for accepted fixes.
- A reviewer must not approve based only on summaries, notifications, or prior review markers.
- Review status must be tied to the current head or active slice through explicit fields such as review status, reviewed SHA, and whether the review applies to the active slice.
- Any later material change invalidates the prior review until the changed state is checked.
- Review does not override the workflow manifest, user approvals, or deterministic gates.
