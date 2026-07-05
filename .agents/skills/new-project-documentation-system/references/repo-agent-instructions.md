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
- `docs/delivery/current-state.md`
- `docs/delivery/active-slice-manifest.json`
- `scripts/agent/session_continuity.py`

Use the files in `assets/` as starting templates, then tailor them to the project.

## Root Instruction Content

Root instructions must include:

- source-of-truth hierarchy
- required reading order
- no-code-until-docs gate when relevant
- branch and PR discipline
- privacy, PHI, and secret handling
- migration and environment-file rules
- validation commands
- how to update docs when code changes
- how every new session runs the session-start gate and confirms the exact next permitted action

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
- current-state path, active-slice manifest path, workflow-manifest path, and first session-start command

## New Chat Prompt

Use the sequential manual prompt family when the user will start each next
session manually. Use the parent/orchestrator prompt family only after the user
explicitly approves centralized parent automation. Parent/orchestrator closeout
must reconcile current PR head, current-head inline comments, issue comments,
required checks, local branch state, and stale-closeout status before reporting
clean completion.

Sequential manual structure:

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

Parent/orchestrator structure:

```text
We are starting a parent/orchestrator run for <Project Name> in Codex.

Repo path:
<absolute repo path>

Run envelope:
- automation_mode: parent_orchestrator
- actor_role: parent
- handoff_target: parent
- objective: <bounded objective>
- allowed_next_slice_rule: <exact next-slice rule>
- maximum child sessions: <positive integer>
- review authority: <review authority>
- publication authority: <none or exact authorized action>
- stop conditions: stop for missing authority, failed validation, required-check blocker, review ambiguity, exhausted child count, unavailable tooling, or user stop.

The parent must not implement product code. It may start one bounded child task at a time, consume child handoffs internally, and continue only while the run envelope independently authorizes the next child task.

Before final parent closeout, record the current PR head, current-head inline comments, issue comments, required checks, local branch state, working-tree state, and stale-closeout status in `docs/delivery/active-slice-manifest.json`, then run `python scripts/agent/session_continuity.py closeout-check`. If current-head inline findings conflict with a later no-major-issues summary, classify review state as ambiguous and stop.
```
