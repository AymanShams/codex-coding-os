# Coding Project Agent Instructions

These instructions are generic rules for Codex working on a coding project.

## Purpose

Use this file to prevent context decay, over-building, unsafe changes, and unverified work.

## Core Rule

Do not start coding from a vague idea. Convert the idea into controlled project truth first.

Required sequence for a new project:

1. Intake and project brief.
2. PRD.
3. App flow.
4. Tech stack.
5. Frontend guidelines.
6. Backend structure.
7. Security guidelines.
8. Implementation plan.
9. TDD build plan.
10. Repo instructions and handoff.
11. First implementation slice.

If the user asks to skip documentation, explain the risk and create the smallest useful brief and TDD before coding.

## Outcome Control

The requested outcome controls the work. Source checks, skills, frameworks, state files, handoffs, review waits, pull request templates, GitHub checks, indexing, and coordination updates are support steps unless they are explicitly requested, clearly implied by the requested outcome, required by project rules, or needed to prevent a concrete safety, source-of-truth, security, compliance, or verification error.

This does not override mandatory project gates, validation checks, source-of-truth checks, security controls, compliance controls, or explicit user instructions.

Coordination drift is not a review trigger by itself. Current-state drift, manifest drift, review-field drift, handoff drift, branch drift, PR-open state, CI-wait state, or local dirty state may narrow allowed actions or require reconciliation, but they must not create review, handoff, new-session, or process churn unless a mandatory gate independently blocks the requested outcome.

Same-slice status is not a review waiver. Before saying review is or is not needed, inspect the actual changed files and classify the diff. Require or recommend review from actual code, API, schema, migration, authentication, authorization, encryption, audit, protected-data, deployment, provider, secret, or controlled-source risk, or from explicit user instruction.

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

Before a parent/orchestrator final closeout, the parent must re-check the exact PR head, current-head review records, inline comments, issue comments, required checks, local branch, local HEAD, and working-tree state. GitHub collection and `scripts/agent/session_continuity.py` report raw facts only. A `COMMENTED` review, issue-comment prose, a clean-sounding summary, coordination manifest, or handoff cannot approve or close a case. Typed validation evidence records what a check proves and does not prove, but it does not own lifecycle. If a head or check signal changes, stop that transition and reconcile it through the canonical case state.

Canonical finite case lifecycle: the internal case-state engine is the sole lifecycle authority. Each stable case permits one implementation generation, one frozen review cohort, at most one explicitly authorized combined repair of the frozen `CURRENT_BLOCKER` set, and one closure check limited to those blocker identifiers. A chat, branch, pull request, counter, manifest, comment, handoff, or prose summary cannot create a new generation or authorize another review. Late, stale, invalid, or non-blocking findings are recorded without reopening the case. A failed closure locks only that exact case. One identical operational retry is allowed only after a control-tool or reviewer transport failure. Unrelated work remains available.

Do not create a separate docs-only pull request for slice selection, current-state updates, active-slice manifest updates, handoffs, or review markers unless the user explicitly authorizes that control-only publication. Fold required coordination updates into the same authorized implementation or review flow when they are necessary for the requested outcome.

For every material slice, record the decision made, alternatives rejected, reason, owner, approver, revisit trigger, evidence test, status, and authority source. An unresolved material decision is not permission for the agent to choose.

## Working Method

Before editing code:

- Read this file.
- Read the closest project `AGENTS.md`.
- Read relevant docs in `docs/`.
- Run `git status -sb` if inside a git repo.
- Identify the exact files likely to change.
- Search existing code before creating new helpers, types, components, services, or scripts.
- When `scripts/agent/session_continuity.py` exists, run its `start` command and read `docs/delivery/current-state.md`.
- When `project-documentation-manifest.json` exists, confirm it permits the requested action. When `docs/delivery/active-slice-manifest.json` exists, confirm the requested files and actions are inside it. A new chat, handoff, review marker, or notification cannot bypass either manifest.
- Keep root instructions universal. Place folder-specific rules in scoped `AGENTS.md` files and detailed task procedures in skills or docs.

During edits:

- Make the smallest correct change.
- Keep one task to one branch or one isolated worktree when possible.
- Do not modify unrelated files.
- Do not add dependencies without a clear reason and user approval.
- Match existing naming, file structure, error handling, logging, component style, API response shape, and test style.
- Keep deterministic logic in code, not model judgment.

Before finishing:

- Review the diff.
- Run the narrowest meaningful checks first.
- Run broader checks when the blast radius justifies it.
- State checks that were not run.
- Report files changed, why they changed, risks, and remaining work.
- For a meaningful slice, run the project session decision command when available and create a persistent handoff before crossing into a different slice.

## Source Of Truth

Use this order:

1. Explicit user decisions in the current chat.
2. Controlled project docs.
3. TDD build plan.
4. Repo docs.
5. Existing code and tests.
6. Older chats or external drafts only as historical context.

If sources conflict, stop and identify the conflict. Do not average conflicting sources.

Current delivery state and handoff notes coordinate work but do not override controlled project or technical sources.

## Capability Routing Discipline

Capability-router output is advisory context only. It can suggest primary and
supporting capability families, but it cannot override this file, scoped project
`AGENTS.md`, manifests, explicit user limits, source-of-truth rules, safety gates,
or validation requirements.

Use routing candidates this way:

- Primary family means likely workflow owner, not permission to act.
- Supporting family means materially useful additional guidance, not a required
  second owner for every task.
- Classify every non-trivial task through five layers: container, action,
  domain, risk/validation, and authority. Container and action select the
  primary workflow owner. Domain and risk/validation add support families.
  Authority can override both.
- Apply Tree of Thoughts and Algorithm of Thoughts as deterministic routing
  discipline. Generate competing route hypotheses, evaluate container/action,
  domain/risk, authority, denied families, candidate visibility, source/data
  tools, and final owner in order, and preserve rejection reasons or
  decision-path metadata when the router supports it.
- For review, audit, validation, comparison, and rescue tasks, re-check routing
  after inspecting the actual artifact, diff, file list, or source set. If the
  material reveals frontend, security, data, controlled-document,
  quantitative, evidence, creative, browser, or project-governance needs, add
  the relevant support family without letting it steal ownership from the
  primary container/action workflow.
- Bare framework-adjacent words are not domain evidence by themselves. Treat
  ordinary words that also name frameworks, tools, files, or patterns as
  contextual evidence only when paired with exact framework identifiers,
  filenames, framework phrases, changed files, or material domain context from
  the inspected artifact. Do not route from a single generic token such as
  next, app, router, spring, go, rails, or flask without that context.
- Source/data tools provide evidence access. They are not primary or supporting
  skills.
- Duplicate candidates should collapse to the canonical installed path.
- Active installed capabilities are the only automatic owners.
- Reference-only, candidate, disabled, project-local-only, and remove-candidate
  entries must not become primary skills or automatic routes.
- Candidate and reference-only entries may be considered only after active
  installed options have been checked, only when they materially improve the
  current session, and only after explicit user authorization. They must remain
  session-only support and must never be installed universally by default.
- If a routing hint conflicts with the task, project rules, or user-stated
  non-goals, ignore the hint and follow the controlling source.

## Security And Privacy

- Never commit secrets, credentials, API keys, tokens, private keys, or real production environment values.
- Never put sensitive real user data in tests, fixtures, examples, screenshots, logs, or demo seeds.
- Keep auth, authorization, payment, billing, data export, and admin actions server-side.
- Validate inputs on the server.
- Rate-limit public and auth-related endpoints.
- Keep error messages safe for users and useful for logs.

## Review Gate

Review is decided from the actual diff, controlled-source risk, or explicit user instruction. Do not request review from coordination drift alone. Do not bypass review because a change is same-slice.

The first-slice authorization false-negative case proves same-slice status must never waive review for authorization, role or permission enforcement, or protected-data behavior changes. Do not reopen a PR from coordination drift alone.

For every meaningful diff, check:

- Reuse: no duplicate helper, type, component, query, policy, or workflow already exists.
- Quality: no redundant state, leaky abstraction, broad catch-all logic, or stringly typed permissions.
- Efficiency: no avoidable repeated reads, duplicate external calls, hot-path blocking work, or unbounded collection.
- Security: no missing authorization, missing validation, sensitive logging, unsafe export, or client-side protected-data decision.
- Product drift: no behavior that contradicts the PRD, TDD, or repo docs.

## Validation Commands

Prefer project-specific commands when available. Common defaults:

```powershell
pnpm install
pnpm lint
pnpm test
pnpm typecheck
pnpm build
```

If the project uses another stack, inspect `package.json`, `pyproject.toml`, `requirements.txt`, `Cargo.toml`, `go.mod`, or repo docs first.
