# <Date> Project Setup And Agent Context

## Project

- Name:
- Repo path:
- Source documentation path:
- Current status:
- Current-state path:
- Active-slice manifest path:
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
1. Run python scripts/agent/session_continuity.py start --start-new.
2. Read AGENTS.md and CLAUDE.md.
3. Read docs/delivery/current-state.md.
4. Read docs/delivery/active-slice-manifest.json.
5. Read docs/index.md.
6. Read docs/history/<handoff file>.
7. Read the controlled TDD.
8. Read project-documentation-manifest.json.
9. Run the workflow manifest validator.
10. Run git status -sb and confirm whether the local repo is synced with origin/main.

If the workflow manifest or active-slice manifest is not ready for coding, continue from its first blocked or incomplete phase. Propose the first implementation slice only when both manifests permit coding.
```
