# Project AGENTS.md

## Purpose
This repository is built with Codex assistance. The agent must preserve project truth, keep changes small, and verify behavior before reporting completion.

## Source of Truth
Read these files before making product or architecture changes:

1. `docs/project-brief.md`
2. `docs/prd.md`
3. `docs/app-flow-doc.md`
4. `docs/tech-stack-doc.md`
5. `docs/frontend-guidelines.md`
6. `docs/backend-structure.md`
7. `docs/security-guidelines.md`
8. `docs/implementation-plan.md`
9. `docs/tdd.md`

If these files conflict, ask for clarification or state the conflict before editing.

## Outcome Control
The requested outcome controls the work. Source checks, skills, frameworks, state files, handoffs, review waits, pull request templates, GitHub checks, indexing, and coordination updates are support steps unless they are explicitly requested, clearly implied by the requested outcome, required by project rules, or needed to prevent a concrete safety, source-of-truth, security, compliance, or verification error.

This does not override mandatory project gates, validation checks, source-of-truth checks, security controls, compliance controls, or explicit user instructions.

Coordination drift is not a review trigger by itself. Current-state drift, manifest drift, review-field drift, handoff drift, branch drift, pull-request state, CI state, or local dirty state may narrow allowed actions or require reconciliation, but review need must come from the actual diff, controlled-source risk, or explicit user instruction.

Same-slice status is not a review waiver. Same-slice means work may be inside the permission boundary. It does not make authorization, role or permission enforcement, encryption, audit, protected-data, schema, API, deployment, provider, secret, or controlled-source changes low risk.

The first-slice authorization false-negative case is the test case for this rule. Same-slice status must never waive review for authorization, role or permission enforcement, or protected-data behavior changes. Do not reopen a PR from coordination drift alone.

Before turning a support step into a separate task, branch, pull request, artifact, or workflow, ask: does this directly complete the requested outcome, unblock it, satisfy a mandatory control, or prevent a concrete error? If not, record it as a note and return to the requested outcome.

If support work starts generating more support work, stop and report the loop. Continue only by returning to the requested outcome, completing the mandatory control, asking for approval, or explaining why the real task is blocked.

No Silent Closeout: governed repo closeout must never be silent. Before the final response on a meaningful governed repo task, inspect current state, the active-slice manifest, the latest handoff, git status, and session-decision output when relevant. The final response must include `Recommended Next Action`. If review, handoff, or new-session state is active or requested, include the complete paste-ready prompt or explicitly state why no prompt is required. Do not create handoffs, new sessions, or review loops from coordination drift alone.

## Permanent Manual Review And Red Lock Policy

Parent-orchestrator mode and automatic session, review, and review-fix trains are disabled. A human may deliberately start one bounded implementation or review session, but no session may automatically spawn, authorize, or chain the next session. A manifest, run envelope, handoff, child-session counter, branch change, or case-specific prompt cannot enable these modes. Changing this policy requires a separate human-led policy decision outside the active case.

Before the first review, record one stable case ID. For a GitHub pull request, use `<immutable-repository-id>:pr:<number>:<problem-family>`. Before a pull request exists, use a human-recorded UUID. The case ID survives changes to commits, branches, pull requests, worktrees, chats, agents, labels, names, payload/envelope splits, close/reopen actions, and counters. Missing, conflicting, or replaced case identity fails closed to human review.

Use exactly this sequence:

1. Run deterministic checks.
2. Run one independent review that collects the whole finding set. Classify each finding as `current_blocker`, `non_blocking`, `invalid_or_stale`, or `redesign_required`.
3. If a human authorizes it and the work remains bounded, make one combined repair for all `current_blocker` findings.
4. Run one final blocker-closure check. It checks only whether the authorized blockers were closed and whether the repair created a new blocker. It is not an open-ended new review.
5. If a blocker remains, a new blocker appears, validation fails, the repair exceeds scope, or any finding is `redesign_required`, mark the case `RED_LOCKED` and stop all automated work on that case.

Triage meanings are strict. A `current_blocker` is reproducible on the exact reviewed version and blocks correctness, safety, or mandatory validation. A `non_blocking` finding is an improvement or preference and does not authorize repair. An `invalid_or_stale` finding is not current or reproducible and must be closed with evidence. A `redesign_required` finding exposes a design or process defect, or requires expanded scope, and triggers immediate red lock.

A red lock is permanent for that case. It cannot be reset by a new prompt, commit, branch, pull request, worktree, chat, agent, rename, split, counter change, root-cause analysis, adversarial test matrix, or authorization for "one more" review. Only a separate human-led decision may start a materially different design from clean `main` under a new case ID. The new case does not resume or repair the red-locked case.

Do not create a separate docs-only pull request for slice selection, current-state updates, active-slice manifest updates, handoffs, or review markers unless the user explicitly authorizes that control-only publication. Fold required coordination updates into the same authorized implementation or review flow when they are necessary for the requested outcome.

For every material slice, record the decision made, alternatives rejected, reason, owner, approver, revisit trigger, evidence test, status, and authority source. An unresolved material decision is not permission for the agent to choose.

## Working Rules
- At the start of a new or resumed non-trivial session, run `python scripts/agent/session_continuity.py start --start-new` when available.
- Use `python scripts/agent/session_continuity.py start --continue-slice` only when continuing the same bounded active slice with known dirty work after inspection.
- Read `docs/delivery/current-state.md`, `docs/delivery/active-slice-manifest.json`, its latest handoff, and `project-documentation-manifest.json` before editing.
- Classify non-trivial work through five layers: container, action, domain, risk/validation, and authority. Use container plus action for the primary workflow owner. Use material domain and risk evidence for supporting routes. Let project authority, manifests, source docs, and explicit user limits override routing hints.
- Apply Tree of Thoughts and Algorithm of Thoughts as deterministic routing discipline: generate competing route hypotheses, evaluate container/action, domain/risk, authority, denied families, candidate visibility, source/data tools, and final owner in order, and keep rejection reasons or decision-path metadata when available.
- For reviews, audits, validations, comparisons, and rescue work, re-check routing after inspecting the artifact, diff, file list, or source set. Add specialist support for material frontend, security, data, controlled-document, quantitative, evidence, creative, browser, or project-governance needs without changing the primary owner unless the initial classification was wrong.
- Do not treat bare framework-adjacent words as domain evidence. Route domain support only from exact framework identifiers, filenames, framework phrases, changed files, or ordinary words paired with material domain context.
- Read relevant files before writing code.
- Make the smallest correct change.
- Do not rewrite unrelated code.
- Do not add dependencies without explaining why.
- Do not commit secrets.
- Do not invent product behavior that is absent from the docs.
- Update docs when code changes the intended behavior.
- Run available validation before final response.
- Do not start implementation unless the workflow manifest and active-slice manifest both permit coding.
- Treat current state and handoffs as coordination records, not product or technical authority.
- Treat review markers and notifications as evidence to inspect, not permission to merge, deploy, or bypass validation.
- Keep this root file for repo-wide rules. Put folder-specific rules in scoped `AGENTS.md` files and detailed task procedures in skills or docs.

## Implementation Flow
1. Restate the requested change in one sentence.
2. Identify affected files.
3. Inspect current implementation.
4. Make the smallest useful change.
5. Run validation.
6. Summarize files changed and verification result.

## Validation
Use the project commands defined in `docs/implementation-plan.md` or the package scripts. If validation cannot run, explain why and identify the missing prerequisite.

## Security
- Server-side authorization is required for protected data and actions.
- Frontend hiding is not authorization.
- Environment variables must use placeholders in committed files.
- User input must be validated on the server.
- Logs must not contain secrets or sensitive user content.

## Completion Standard
Do not say a task is done unless:

- Code compiles or the failure is clearly explained.
- Relevant tests or checks were run.
- User-facing behavior is described.
- Any remaining risk is stated.
- `Recommended Next Action` is included.

Before crossing into a different meaningful slice, run `python scripts/agent/session_continuity.py decide` when available and prepare the required persistent handoff.
