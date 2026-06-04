---
name: ai-coding-discipline-pack
description: Use when starting, planning, reviewing, or rescuing AI-assisted coding work, especially vibe coding, new app builds, multi-agent coding, long sessions, refactors, or avoiding scope and architecture drift.
---

# AI Coding Discipline Pack

Use this skill to keep Codex work bounded, source-aware, and verified.

## First Decision

Classify the task:

| Task Shape | Workflow |
|---|---|
| Tiny edit | Read target file, edit, run narrow check |
| Feature | Request, spec, plan, execute, verify |
| New project | Use `codex-coding-os-master` first |
| Refactor | Define boundary, preserve behavior, verify before and after |
| Bug | Reproduce, isolate cause, fix minimally, add regression check |
| Multi-agent | One branch or worktree per agent and one bounded task per agent |

## Core Rules

1. Think before coding.
2. Simplicity first.
3. Surgical changes.
4. Goal-driven execution.
5. Deterministic logic belongs in code.
6. Use checkpoints.
7. Surface conflicts.
8. Read before writing.
9. Tests must prove behavior.
10. Convention beats novelty.
11. Fail visibly.

## Context Gate

Before editing:

- read `AGENTS.md`
- read relevant docs
- inspect target file
- inspect imports and direct callers
- inspect relevant tests
- search for existing helpers
- run `git status -sb` if in a repo

## Planning Standard

For material work, state:

- goal
- scope
- non-goals
- affected files
- risk
- verification
- rollback or stop condition

## Execution Rules

- Do one bounded step at a time.
- Do not touch unrelated files.
- Do not add speculative abstractions.
- Do not add dependencies without approval.
- Prefer existing project patterns.
- Update docs when API, schema, config, security, deployment, or user-visible workflow changes.

## Verification Rules

Run the narrowest meaningful checks first:

- changed unit tests
- typecheck
- lint
- build
- integration test
- browser check
- migration validation

If a check is missing or blocked, say so.

## External Agent Safety

Use this policy for any external agent:

```text
Mode: Manual approval or smart approval
Branch: New branch or isolated worktree
Data: No secrets, no production credentials, no sensitive personal data
Scope: One bounded task per session
Rule: Agent plans first and waits for approval before edits
Review: Human or Codex reads diff and rationale before merge
```

## Anti-Patterns

Stop when you see:

- one giant prompt to build the whole app
- coding before source docs
- new auth provider without approval
- new database or hosting provider without approval
- broad refactor hidden inside feature work
- passing tests with shallow assertions
- unverified "done"

