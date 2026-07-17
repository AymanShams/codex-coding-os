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
- Parent/orchestrator final closeout records the exact PR head, current-head review records, inline comments, issue comments, required checks, local branch, local HEAD, and working-tree state as raw evidence. GitHub collection and the continuity helper do not own lifecycle.
- A `COMMENTED` review, issue-comment prose, clean-sounding summary, coordination manifest, handoff, or metadata-only PR edit cannot approve, close, or reopen a case.
- The internal case-state engine is the sole lifecycle authority. Each stable case permits one implementation generation, one frozen review cohort, at most one explicitly authorized combined repair of the frozen `CURRENT_BLOCKER` set, and one closure check limited to those blocker identifiers.
- Chat, branch, PR, counter, manifest, comment, handoff, or prose changes cannot authorize another generation. Late, stale, invalid, or non-blocking findings are recorded without reopening. A failed closure locks only that case, one identical operational retry is allowed only for a control failure, and unrelated work remains available.

## Control-State Contract

The control layer must prove authority from live state, not from PR-body claims, stale current-state files, or assistant summaries.

Required invariants:

- Current-head review authority is valid only when at least one current-head review has an accepted state. Accepted states are `APPROVED` and `COMMENTED`. `CHANGES_REQUESTED` and `PENDING` block closeout. `DISMISSED` is not authority, so a dismissed-only current-head review also blocks closeout.
- Inline review comments must be current-head and unresolved before they block. Until the helper can fetch GitHub review-thread resolution, current-head inline comments remain blockers unless explicit current-head disposition evidence is recorded.
- Required checks are the blocking check set. Optional visible checks may be reported, but they must not block unless the repository marks them required.
- Parent or publication closeout requires a clean live worktree. Dirty local state can be reported only as a blocker, never as matching completion evidence.
- Exact-head PR-body metadata must be line-anchored. Current PR head and reviewed head values must be full 40-character commit SHAs. Blank fields must not capture text from the next line.
- Current-state and active-slice manifest evidence must match the live branch and PR head before it is used for slice selection, review authority, publication, or parent closeout.
- Python and JavaScript helpers must enforce the same evidence schema, accepted states, blocker states, malformed-field behavior, and adversarial fixtures.
- Malformed manifests, malformed PR-body fields, nulls, booleans, copied commit IDs, stale SHAs, duplicate comments, and missing fields fail closed without throwing tracebacks.

Adversarial matrix required before another automated review loop:

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
