# Validation Gates

Use these gates before reporting a new project documentation setup as complete.

## Required Checks

1. File presence: expected controlled docs, repo docs, instruction files, and handoff note exist.
2. Source inventory: sources were classified and sensitive files were flagged.
3. Source-lock: filled docs name controlling sources or clearly identify current assumptions.
4. Stage fit: Stage 0 to Stage 3 are filled when sources exist, and Stage 4 to Stage 6 are not falsely completed before evidence exists.
5. Drift: high-risk decisions match across PRD, app flow, tech stack, frontend, backend, security, implementation plan, TDD, and repo docs.
6. Generic filler: no `TODO`, `TBD`, `starter draft`, placeholder brackets, or vague `as needed` language in filled docs.
7. Name hygiene: old project names are removed from file names, Markdown, YAML, JSON, env examples, README files, and generated DOCX internals when applicable.
8. Secret hygiene: no obvious credentials, generated passwords, tokens, or `.env` values are staged.
9. sensitive regulated data hygiene: no sensitive regulated information, private user records, or private personal files are staged for Git.
10. Repo hygiene: `.gitignore`, PR template, CODEOWNERS or owner mapping, and governance checks exist where practical.
11. Agent context: root and scoped `AGENTS.md`, `CLAUDE.md`, docs index links, and handoff note exist.
12. Git state: run and report `git status -sb` when a repo exists.

## Contradiction Patterns

Search for contradictions around:

- shared versus independent database
- auth provider and MFA model
- Supabase Auth if prohibited
- Vercel or Supabase as development-only versus permanent architecture
- role visibility and admin access
- sensitive regulated data, encryption, audit, and break-glass access
- AI autonomy and human approval gates
- WhatsApp, email, notifications, and regulated content rules
- integration ownership and source-of-truth sync direction

## Final Report

Report:

- created or updated paths
- checks passed
- checks not run and why
- material blockers
- remaining decisions before coding

Do not claim completion if a critical validation gate was skipped without explanation.

