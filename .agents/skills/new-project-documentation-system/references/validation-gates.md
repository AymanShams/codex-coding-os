# Validation Gates

Use these gates before reporting a new project documentation setup as complete.

## Required Checks

1. Workflow manifest: `scripts/validate_workflow_manifest.py` passes for the current mode and next action.
2. File presence: expected controlled docs, repo docs, instruction files, and handoff note exist.
3. Source inventory: sources were classified and sensitive files were flagged.
4. Source-lock: filled docs name controlling sources or clearly identify current assumptions.
5. Approval state: material decisions, controlled docs, TDD, and coding-start approvals are recorded when required.
6. Stage fit: Stage 0 to Stage 3 are filled when sources exist, and Stage 4 to Stage 6 are not falsely completed before evidence exists.
7. Drift: high-risk decisions match across project brief, PRD, app flow, tech stack, frontend, backend, security, implementation plan, TDD, and repo docs.
8. Generic filler: run `scripts/validate_filled_artifacts.py` against filled docs. Blank source templates are excluded from this check.
9. Name hygiene: old project names are removed from file names, Markdown, YAML, JSON, env examples, README files, and generated DOCX internals when applicable.
10. Secret hygiene: no obvious credentials, generated passwords, tokens, or `.env` values are staged.
11. PHI hygiene: no protected health information, pilot member records, or private medical files are staged for Git.
12. Repo hygiene: `.gitignore`, review workflow, ownership mapping where needed, and governance checks exist where practical.
13. Agent context: root and scoped `AGENTS.md`, `CLAUDE.md`, docs index links, current-state file, session continuity command, and handoff note exist.
14. Git state: run and report `git status -sb` when a repo exists.
15. Session gate: a new session cannot reach implementation unless the workflow manifest permits coding.

## Contradiction Patterns

Search for contradictions around:

- shared versus independent database
- auth provider and MFA model
- Supabase Auth if prohibited
- Vercel or Supabase as development-only versus permanent architecture
- role visibility and admin access
- PHI, encryption, audit, and break-glass access
- AI autonomy and human approval gates
- WhatsApp, email, notifications, and clinical content rules
- integration ownership and source-of-truth sync direction

## Final Report

Report:

- created or updated paths
- checks passed
- checks not run and why
- material blockers
- remaining decisions before coding

Do not claim completion if a critical validation gate was skipped without explanation.
