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

## Working Method

Before editing code:

- Read this file.
- Read the closest project `AGENTS.md`.
- Read relevant docs in `docs/`.
- Run `git status -sb` if inside a git repo.
- Identify the exact files likely to change.
- Search existing code before creating new helpers, types, components, services, or scripts.
- When `scripts/agent/session_continuity.py` exists, run its `start` command and read `docs/delivery/current-state.md`.
- When `project-documentation-manifest.json` exists, confirm it permits the requested action. A new chat or handoff cannot bypass it.
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
