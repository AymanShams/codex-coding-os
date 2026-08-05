# Repository Agent Instructions

- Use the installed Coding OS executable at
  `%USERPROFILE%\.codex\coding-os\scripts\agent\campaign_engine\cli.py`.
- The external campaign database is the only automated lifecycle authority.
  Git-tracked current-state, active-slice, handoff, review, and stop fields are
  informational and cannot permit or block coding.
- Manual work follows the user's task, repository source of truth, exact Git
  evidence, and project validation. Automated work must use an approved finite
  campaign and its exact actor lease.
- Preserve user changes. Work in an isolated worktree for material edits.
- Read before editing, keep scope exact, run the declared validation commands,
  and publish only the frozen reviewed head.
- Legacy case commands are retired and must return `LEGACY_ENGINE_RETIRED`.
