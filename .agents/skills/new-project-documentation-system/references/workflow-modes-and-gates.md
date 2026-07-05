# Workflow Modes And Gates

## Modes

| Mode | Use when | Completion meaning |
|---|---|---|
| Full Run | The skill is explicitly invoked for a new project or source folder without a narrower limit | All phases 0 to 8 pass |
| Review Only | The user asks to review, audit, compare, or identify gaps without changing files | Review findings delivered, no drafting or completion claim |
| Single Phase | The user explicitly asks for one deliverable or phase only | Requested phase completed, wider workflow remains incomplete |
| Resume | A valid workflow manifest exists from a prior run | Continue from the first incomplete or blocked phase |

Do not infer Single Phase merely because the user mentions one folder or one document. An explicit invocation of the skill defaults to Full Run.

## Status Values

Use only:

- `not_started`
- `in_progress`
- `blocked`
- `awaiting_approval`
- `approved`
- `completed`
- `explicitly_deferred`

Only one phase may be `in_progress`.

## Hard Gates

### Gate A: Scope

Do not inventory or draft until the mode, output location, required formats, and code permission are recorded.

### Gate B: Source Authority

If sources conflict on a material issue, stop and ask the user which source controls. Repetition across generated files is not independent confirmation.

### Gate C: Material Decisions

Do not draft the PRD while material decisions remain open. Consolidate questions into one numbered decision request.

### Gate D: Controlled Docs

Treat all seven documents as drafts until approved. Do not use them as source truth for repo docs or code before approval.

### Gate E: TDD Alignment

Do not call a TDD merged unless every external draft was classified by keep, correct, reject, or defer. Do not advance while TDD conflicts with controlled docs.

### Gate F: Repo And Instructions

Do not recommend coding before repo documentation and agent instructions exist. If no repo exists, ask for a decision rather than skipping the phase.

### Gate G: Final Validation

Run the manifest validator and artifact validation. Any blocker or major defect prevents a ready-to-code claim.

### Gate H: Session Continuity

Every new or resumed non-trivial session must read the workflow manifest, current delivery state, active-slice manifest, latest handoff, and controlling sources before editing. If current state or a handoff implies coding while either manifest does not permit it, the blocking manifest wins and the session must continue from the first blocked or incomplete phase.

## Approval Rules

Record approval only from:

- an explicit user statement
- a controlling source that clearly grants the decision
- explicit delegated authority from the user

Silence, lack of response, or Codex inference is not approval.

A handoff, new chat prompt, coordination-state update, review marker, or review notification is also not approval.

Coordination drift is not a review trigger by itself. Current-state drift, manifest drift, review-field drift, handoff drift, branch drift, PR-open state, CI-wait state, or local dirty state may require inspection or reconciliation, but review need comes from actual diff risk, controlled-source risk, or explicit user instruction. Same-slice status is not a review waiver.

Parent/orchestrator final closeout requires one last live-state reconciliation: current PR head, current-head inline comments, issue comments, required checks, local branch state, working-tree state, stale-closeout status, and publication stabilization evidence. Publication stabilization evidence records PR body head metadata, reviewed-head evidence, exact review authority count, post-review-fix reconciliation status, and metadata-only PR body check retrigger state. After any review-fix push, reconcile those fields before starting another review or publication child. Metadata-only PR body edits that retrigger required checks are bounded wait states, not new review triggers by themselves. Continue only while code head, PR body head, reviewed-head evidence, and local HEAD remain equal. If current-head inline findings conflict with a later no-major-issues summary, the review state is ambiguous and the parent must stop until the finding is fixed, proven stale with evidence, or explicitly resolved by the review authority.
