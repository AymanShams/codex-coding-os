# <Date> Project Setup And Agent Context

## Project

- Name:
- Repo path:
- Source documentation path:
- Current status:
- Current-state path:
- Workflow-manifest path:
- Exact next permitted action:

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
1. Run python scripts/agent/session_continuity.py start.
2. Read AGENTS.md and CLAUDE.md.
3. Read docs/delivery/current-state.md.
4. Read docs/index.md.
5. Read docs/history/<handoff file>.
6. Read the controlled TDD.
7. Read project-documentation-manifest.json.
8. Run the workflow manifest validator.
9. Run git status -sb and confirm whether the local repo is synced with origin/main.

If the workflow manifest is not ready for coding, continue from its first blocked or incomplete phase. Propose the first implementation slice only when the manifest permits coding.
```
