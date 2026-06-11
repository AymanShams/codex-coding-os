# <Date> Project Setup And Agent Context

## Project

- Name:
- Repo path:
- Source documentation path:
- Current status:

## Source Of Truth

1.
2.
3.

## Decisions Made

- Product:
- Architecture:
- Data:
- Security:
- Integrations:
- Deployment:

## Sensitive Data Rules

- Do not commit:
- Keep local only:
- Required scans:

## Validation Completed

- Files:
- Drift:
- Secrets:
- PHI:
- Git:

## Known Blockers

- 

## New Chat Prompt

```text
We are starting implementation for <Project Name> in Codex.

Repo path:
<absolute repo path>

Before coding:
1. Read AGENTS.md and CLAUDE.md.
2. Read docs/index.md.
3. Read docs/history/<handoff file>.
4. Read the controlled TDD.
5. Read project-documentation-manifest.json.
6. Run the workflow manifest validator.
7. Run git status -sb and confirm whether the local repo is synced with origin/main.

If the workflow manifest is not ready for coding, continue from its first blocked or incomplete phase. Propose the first implementation slice only when the manifest permits coding.
```
