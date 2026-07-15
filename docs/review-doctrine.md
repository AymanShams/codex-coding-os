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
- Parent-orchestrator mode and automatic session, review, and review-fix trains are disabled. Every session is deliberately started by a human and returns control to the human when its bounded task ends.
- Before the first review, record one stable case ID. For a GitHub pull request, use the immutable repository ID, pull-request number, and problem-family key. Before a pull request exists, use a human-recorded UUID. The identity survives commit, branch, pull-request, worktree, chat, agent, rename, split, close/reopen, and counter changes.
- Run one independent review that collects the complete finding set, one human-authorized combined repair for current blockers, and one final blocker-closure check. The final check is not an open-ended new review.
- Classify findings as `current_blocker`, `non_blocking`, `invalid_or_stale`, or `redesign_required`. Repair only current blockers. Close stale or invalid findings with evidence. Redesign-required findings trigger immediate red lock.
- Conflicting GitHub review signals are not a pass. If current-head inline findings conflict with a later no-major-issues summary, classify review state as ambiguous and stop until the finding is fixed, proven stale with evidence, or explicitly resolved by the review authority.
- If any blocker remains, a new blocker appears, required validation fails, repair exceeds scope, or redesign is required, mark the stable case `RED_LOCKED` and stop all automated work on it. Root-cause analysis and adversarial tests may explain failure but cannot authorize another review.
- A red lock cannot be reset by a new prompt, commit, branch, pull request, worktree, chat, agent, rename, split, close/reopen action, or counter change. Only a separate human-led decision may start a materially different design from clean `main` under a new case ID.

## Control-State Contract

The control layer must prove authority from live state, not from PR-body claims, stale current-state files, or assistant summaries.

Required invariants:

- Current-head review authority is valid only when at least one current-head review has an accepted state. Accepted states are `APPROVED` and `COMMENTED`. `CHANGES_REQUESTED` and `PENDING` block closeout. `DISMISSED` is not authority, so a dismissed-only current-head review also blocks closeout.
- Inline review comments must be current-head and unresolved before they block. Until the helper can fetch GitHub review-thread resolution, current-head inline comments remain blockers unless explicit current-head disposition evidence is recorded.
- Required checks are the blocking check set. Optional visible checks may be reported, but they must not block unless the repository marks them required.
- Publication closeout requires a clean live worktree. Dirty local state can be reported only as a blocker, never as matching completion evidence.
- Exact-head PR-body metadata must be line-anchored. Current PR head and reviewed head values must be full 40-character commit SHAs. Blank fields must not capture text from the next line.
- Current-state and active-slice manifest evidence must match the live branch and PR head before it is used for slice selection, review authority, or publication.
- Python and JavaScript helpers must enforce the same evidence schema, accepted states, blocker states, malformed-field behavior, and adversarial fixtures.
- Malformed manifests, malformed PR-body fields, nulls, booleans, copied commit IDs, stale SHAs, duplicate comments, and missing fields fail closed without throwing tracebacks.

Adversarial policy-test matrix. These cases test the guard and never authorize another review:

| Case | Expected behavior |
| --- | --- |
| Current-head `CHANGES_REQUESTED` review with no inline comments | Block closeout |
| Current-head `PENDING` review with no inline comments | Block closeout |
| Current-head `DISMISSED` review with no accepted current-head review | Block closeout |
| Current-head `APPROVED` or `COMMENTED` review, no blockers, required checks passing | Pass review-state gate |
| Current-head unresolved inline comment | Block closeout |
| Stale inline comment from an older head | Do not block |
| Commit-id-only current-head inline comment | Block closeout |
| Dirty live worktree with recorded dirty state | Block final closeout |
| PR-body blank `Review source` followed by another field | Fail PR-body check |
| PR-body abbreviated reviewed SHA | Fail exact-head metadata check |
| Stale current-state branch or slice evidence | Cannot authorize slice selection or publication |
| Malformed manifest object or PR-body field type | Fail closed without crashing |
