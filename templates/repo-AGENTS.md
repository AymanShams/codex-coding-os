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

Opt-in Automation Coding Mode: automation is off by default. It can run only after explicit approval of the repo, run envelope, thread or worktree creation, review expectations, stop conditions, and publication authority. The run envelope must state the objective, allowed next-slice rule, maximum child sessions, branch or worktree plan, review authority, publication authority, handoff target, and stop conditions.

Automation has two valid shapes:
- Sequential manual train: the current session stops with one exact paste-ready next prompt.
- Parent/orchestrator mode: the parent coordinates approved child sessions or worktrees and consumes child handoffs internally unless a stop condition fires.

The orchestrator is admin-only: it may inspect, assign, monitor, verify, reconcile, and report. It may start one bounded child chat or worktree for one approved slice, one review-only pass, one review-fix pass, or one explicitly authorized merge or publication step, but it must not code, choose unapproved slices, bypass review, merge, deploy, or treat child output as authority. Each child must rerun the project start gate, read live sources, inspect Git and PR state, check GitHub automated Codex reviews and actionable inline comments when a PR exists, refresh or report the code graph when the repo uses it and the diff or risk warrants it, then stop with `Recommended Next Action`.

When parent/orchestrator mode is active and the run envelope still authorizes continuation, a child handoff or new-session trigger is an internal transition artifact, not a reason to push a generic prompt back to the user. The parent must continue only to the next independently authorized child task. If the next action is not independently authorized, a human decision is required, the maximum child count is reached, tooling is unavailable, validation or review blocks continuation, or the user says stop, stop instead of creating another chat, handoff, or review loop.

Do not create a separate docs-only pull request for slice selection, current-state updates, active-slice manifest updates, handoffs, or review markers unless the user explicitly authorizes that control-only publication. Fold required coordination updates into the same authorized implementation or review flow when they are necessary for the requested outcome.

For every material slice, record the decision made, alternatives rejected, reason, owner or approver, revisit trigger, and evidence test. An unresolved material decision is not permission for the agent to choose.

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
