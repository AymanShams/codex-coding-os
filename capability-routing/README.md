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

Tests must bind all Codex home, manifest, policy, configuration, schema, registry, and project-map paths to temporary directories before importing or invoking the reference runtime.
