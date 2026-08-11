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
- For an explicit security task, classify the surface before selecting a
  capability. Use a diff scan for Git-backed changes, a standard scan for an
  ordinary repository or path review, and a deep scan only when exhaustive or
  multi-pass work is explicitly requested.
- Use Supabase capabilities only for Supabase surfaces. Use Neon Postgres plus
  provider-neutral PostgreSQL guidance for Neon. Use provider-neutral
  PostgreSQL guidance when no provider plugin owns the database surface.
- Keep client-side checks non-authoritative. Enforce protected actions,
  authorization, secrets, and privileged database operations on a trusted
  server or database boundary.
- Do not copy plugin-managed skills or credentials into the repository. Do not
  claim live provider verification unless a successful read proves the intended
  project and current state.
