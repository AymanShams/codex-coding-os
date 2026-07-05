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

1. Classify the five task layers: container, action, domain, risk/validation, and authority.
2. Check source faithfulness and unsupported decisions.
3. Challenge assumptions, evidence quality, and hidden scope.
4. Inspect the actual artifact or diff, then re-check whether the inspected material requires specialist support.
5. Keep the primary owner tied to the container/action, but add domain and risk lenses when the diff proves them.
6. Check intended behavior, negative cases, and failure modes.
7. Distinguish blocking defects from optional improvements.
8. State what was not reviewed or could not be verified.
9. Avoid rewriting unless the review request includes remediation.

Domain and risk lenses include frontend, security, data, controlled documents, quantitative logic, evidence/source quality, creative artifacts, browser rendering, project governance, and any project-specific source-of-truth boundary. They are not named-skill triggers. They are first-principles checks derived from what changed and what could fail.

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
- Parent/orchestrator final closeout must reconcile the current PR head, current-head inline comments, issue comments, required checks, local branch state, working-tree state, stale-closeout status, and publication stabilization evidence before reporting clean completion.
- Publication stabilization evidence must show that PR body head metadata, reviewed-head evidence, exact review authority count, and required checks were reconciled after the latest review-fix push and before any further review or publication child.
- Metadata-only PR body edits that retrigger required checks are bounded wait states, not new review triggers by themselves. Continue waiting only while the code head, PR body head, reviewed-head evidence, and local HEAD remain equal. Stop if the check stays pending past the bound or any head, review, or check signal changes.
- Conflicting GitHub review signals are not a pass. If current-head inline findings conflict with a later no-major-issues summary, classify review state as ambiguous and stop until the finding is fixed, proven stale with evidence, or explicitly resolved by the review authority.
