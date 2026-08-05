# Project Agent Instructions

## Start

1. Read this file and the closest scoped `AGENTS.md`.
2. Read `docs/index.md`, the controlled TDD, and the task-controlling stable sources.
3. Treat `project-documentation-manifest.json` as a documentation ledger only.
4. Inspect the exact Git root, remote, branch, HEAD, and working-tree state before editing.
5. Identify the requested outcome, allowed paths, non-goals, and validation commands.

## Coding OS

- Manual work follows the user's current request, stable project sources, and repository validation.
- Automated work uses `%USERPROFILE%\.codex\coding-os\scripts\agent\campaign_engine\cli.py`.
- Query automation with `python <installed-cli> --json status --repository-root .`.
- Admit an approved automation specification with `python <installed-cli> --json admit --spec <path>`.
- Repository manifests, current-state files, active-slice files, work summaries, reviews, branches, pull requests, and chats are informational only.
- Do not duplicate automated execution rules in repository prose, hooks, or adapters.

## Source Of Truth

Use this order:

1. Explicit user decisions in the current task.
2. Stable product documentation.
3. Controlled TDD.
4. Repository documentation.
5. Existing code and tests.
6. Older chats and work summaries as historical context only.

## Work Rules

- Preserve user changes and do not modify unrelated files.
- Resolve source conflicts before changing dependent behavior.
- Reuse existing code and conventions before creating new abstractions.
- Update stable documentation when behavior, interfaces, schemas, configuration, or deployment changes.
- Run focused validation first, then broader checks justified by the diff.
- Report exact files, commands, results, Git identity, and checks not run.
- Never commit credentials, tokens, private keys, real environment values, or local campaign state.
