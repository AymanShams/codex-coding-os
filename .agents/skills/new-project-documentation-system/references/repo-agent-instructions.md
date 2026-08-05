# Repository Agent Instructions

Use this reference when preparing a repository for Codex, Claude Code, or human contributors.

## Files To Add

Add only the files appropriate to the repository structure:

- `AGENTS.md`
- `CLAUDE.md`
- scoped `AGENTS.md` files
- `docs/index.md`
- stable product, architecture, interface, data, testing, and delivery documentation
- `project-documentation-manifest.json` as a non-authoritative documentation ledger
- an optional informational work summary

Use the files in `assets/` as starting templates and tailor them to the project.

## Root Instruction Content

Root instructions should include:

- source-of-truth hierarchy
- required stable reading
- exact repository identity checks
- branch and pull-request discipline
- environment and secret handling
- project validation commands
- documentation update expectations
- the installed campaign CLI path and the distinction between manual and automated work

Do not embed campaign transitions or permission logic in `AGENTS.md`, `CLAUDE.md`, hooks, or repository delivery files.

## Informational Work Summary

A work summary may record:

- project and repository identity
- stable documents created or changed
- accepted decisions and their owning source
- changed files
- validation commands and exact results
- known documentation gaps
- exact campaign receipt identifiers when automation was used

It must state that it is informational and cannot authorize, stop, review, repair, publish, or select work. Do not commit volatile campaign snapshots into Git.

## Manual Continuation Prompt

```text
Continue <Project Name> in:
<absolute repo path>

Read AGENTS.md, the closest scoped instructions, docs/index.md, the controlled TDD, and the task-controlling stable sources. Inspect the exact Git root, branch, HEAD, and working tree. Complete only the user's current requested outcome and run the repository's declared validation.
```

## Automated Campaign Prompt

```text
Use the installed Coding OS campaign engine for <Project Name>.

Repository:
<absolute repo path>

Campaign specification:
<absolute specification path>

First run:
python <installed-cli> --json doctor
python <installed-cli> --json admit --spec <absolute specification path>

Return the exact campaign ID and specification digest for user approval. After approval, run only the public command named by the engine receipt. Do not infer authority from repository manifests, work summaries, chats, branches, or pull requests.
```
