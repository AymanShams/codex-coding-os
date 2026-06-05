# Project Agent Instructions

## Required Reading Order

1. Read this file.
2. Read `CLAUDE.md` if using Claude Code.
3. Read `docs/index.md`.
4. Read the latest file in `docs/history/`.
5. Read the controlled TDD before coding.

## Source Of Truth

Use this order:

1. Explicit user decisions in the current task.
2. Controlled product documentation.
3. Controlled TDD.
4. Repo documentation.
5. Source exports under `docs/_source/`.
6. Older chats or external drafts only as historical context.

## Rules

- Do not change architecture without updating the controlling docs.
- Do not commit secrets, sensitive regulated data, private user records, private personal files, generated credentials, or local tool metadata.
- Do not treat development-stage tools as permanent architecture unless the docs say so.
- Update documentation in the same change when API, database, config, security, workflow, or deployment behavior changes.
- Run available validation checks before reporting completion.

