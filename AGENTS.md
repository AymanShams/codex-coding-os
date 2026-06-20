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

Before turning a support step into a separate task, branch, pull request, artifact, or workflow, ask: does this directly complete the requested outcome, unblock it, satisfy a mandatory control, or prevent a concrete error? If not, record it as a note and return to the requested outcome.

If support work starts generating more support work, stop and report the loop. Continue only by returning to the requested outcome, completing the mandatory control, asking for approval, or explaining why the real task is blocked.

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
