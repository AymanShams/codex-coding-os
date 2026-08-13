# Capability routing reference source

This directory is the repository single source of truth for routing decisions and a dormant, public-safe reference port of the universal Codex router architecture.

It is not an installed or active router. The package installer must not copy this directory into a user's Codex home, register its entry points in `hooks.json`, execute the manifest builder, create a route registry, or replace universal routing state. Deployment to a universal layer requires separate explicit authorization.

## Contents

- `routing-policy.yaml` owns ordered repository routing decisions.
- `reference-runtime/` preserves the manifest-and-policy consumer architecture for tests and future reviewed deployment.
- `builder/` is deployment reference source only. It requires explicit inventory inputs and must not be run by package installation.
- The JSON schemas define the active manifest, policy, route decision, authority receipt, and deployment-owned project scope map contracts.
- `project-scope-map.example.json` is synthetic. A real deployment supplies an external map through `CODEX_PROJECT_SCOPE_MAP_PATH`.
- `provenance.json` records the frozen upstream snapshot and repository adaptations.

A deployment must inventory plugin executable and integration roots separately
from managed prompt-skill roots. A plugin package can expose an MCP or app from
its package root while exposing skills from a versioned `skills` root. Finding
the executable surface does not prove that the prompt-skill inventory is
complete, and finding the skill root does not prove that the executable
surface is callable.

Project root maps may contain valid parent and nested roots. Resolution uses
the longest normalized matching root first so a broad workspace root cannot
mask a more specific project root. Only an exact normalized root assigned to
different project IDs is invalid and must fail closed.

The reference runtime treats manifest and configuration presence as
configured-only evidence. A live dependency is callable only when complete task
input supplies a successful `live_call` probe for that dependency, bound to the
same `execution_request_id`. The normalized routing prompt, task instruction,
and optional bounded task text must also match. Missing, failed, or rebound
probes select the declared
fallback. An explicit static-only or no-live instruction overrides a callable
probe. Requests that span multiple provider or frontend security surfaces,
multiple lifecycle phases, or multiple tracker destinations fail closed with a
split-task reason instead of silently selecting the first ordered rule.

Runtime state is intentionally excluded. This repository never ships `active-capabilities.json`, `route-decisions.sqlite3`, live authority receipts, plugin cache contents, authentication state, user configuration, or private project paths.

The reference runtime uses route-decision schema 3.0 and SQLite registry version 3. Every executable receipt binds the exact active-manifest and routing-policy content hashes. Verification reloads both current authorities and rejects a receipt after either authority changes, even if a human-readable snapshot label was reused. Migration from an older registry version atomically purges its receipts.

Tests must bind all Codex home, manifest, policy, configuration, schema, registry, and project-map paths to temporary directories before importing or invoking the reference runtime.

Repository contract tests install the exact dependency pinned in
`capability-routing/requirements-test.txt`. The dormant runtime is not added to
the installed package and does not add a runtime dependency to Coding OS.
