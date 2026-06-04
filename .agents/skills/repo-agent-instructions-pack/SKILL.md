---
name: repo-agent-instructions-pack
description: Use when preparing a repository so Codex, Claude Code, or another coding agent can work without context decay, including AGENTS.md, scoped AGENTS files, CLAUDE.md, handoff notes, and new-chat prompts.
---

# Repo Agent Instructions Pack

Use this skill when a project needs durable agent instructions.

## Files To Add

Add files only where relevant:

- `AGENTS.md`
- `CLAUDE.md`
- `docs/AGENTS.md`
- `apps/web/AGENTS.md`
- `apps/mobile/AGENTS.md`
- `apps/api/AGENTS.md`
- `apps/workers/AGENTS.md`
- `packages/AGENTS.md`
- `database/AGENTS.md`
- `docs/history/<date>-project-setup-and-agent-context.md`

## Root AGENTS Content

Include:

- purpose
- scope and precedence
- required reading order
- source-of-truth hierarchy
- no-code-until-docs gate for new projects
- branch and PR discipline
- secret and sensitive-data handling
- validation commands
- docs update rules
- review gate
- handoff rules

## Scoped AGENTS Content

For each subtree, include:

- what the folder owns
- required docs
- allowed and forbidden behavior
- validation commands
- update rules

## CLAUDE.md Content

Use it only as a bridge:

- state that Claude Code users must follow the same repo rules
- point to `AGENTS.md`
- list required reading order
- state commit, push, deployment, and review boundaries

## Handoff Note Content

Include:

- repo path
- current status
- docs created
- decisions made
- sensitive-data exclusions
- validation completed
- known blockers
- paste-ready new-chat prompt

## Generic New Chat Prompt

```text
We are starting implementation in Codex.

Repo path:
<absolute repo path>

Before coding:
1. Read AGENTS.md.
2. Read docs/index.md.
3. Read the latest docs/history handoff note.
4. Read the controlled TDD.
5. Run git status -sb.

Start by reviewing the repository structure and propose the first implementation slice from the source docs. Do not write code until you identify the exact files, docs, tests, and validation commands needed for that slice.
```

## Validation

Before finishing:

- confirm instruction files exist
- confirm links from docs index if useful
- check no private names or secrets were added
- run `git status -sb`
- summarize changed files

