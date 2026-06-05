# Repo Agent Instructions

Use this reference when preparing a repo so Codex, Claude Code, or another coding agent can continue without context decay.

## Files To Add

Add these when the matching directories exist:

- `AGENTS.md`
- `CLAUDE.md`
- `docs/AGENTS.md`
- `apps/web/AGENTS.md`
- `apps/mobile/AGENTS.md`
- `apps/api/AGENTS.md`
- `apps/workers/AGENTS.md`
- `packages/AGENTS.md`
- `supabase/AGENTS.md`
- `docs/history/<date>-project-setup-and-agent-context.md`

Use the files in `assets/` as starting templates, then tailor them to the project.

## Root Instruction Content

Root instructions must include:

- source-of-truth hierarchy
- required reading order
- no-code-until-docs gate when relevant
- branch and PR discipline
- privacy, sensitive regulated data, and secret handling
- migration and environment-file rules
- validation commands
- how to update docs when code changes

## Handoff Note Content

The handoff note must include:

- project summary
- repo path
- source docs created
- technical decisions
- sensitive-data exclusions
- GitHub/Supabase/Vercel setup status when applicable
- validation results
- known blockers
- paste-ready prompt for the next chat

## New Chat Prompt

Use this structure:

```text
We are starting implementation for <Project Name> in Codex.

Repo path:
<absolute repo path>

Before coding:
1. Read AGENTS.md and CLAUDE.md.
2. Read docs/index.md.
3. Read docs/history/<handoff file>.
4. Read the controlled TDD.
5. Run git status -sb and confirm whether the local repo is synced with origin/main.

Start by reviewing the repository structure and propose the first implementation slice from the source docs.
```

