# ADR 0004: Canonical Capability Routing and Security Plugin Boundary

- Status: Accepted
- Date: 2026-08-11
- Owner: Codex Coding OS implementation owner
- Approver: Ayman Shams
- Authority source: The user's 2026-08-11 instruction to update the repository router first, add complete security routing, keep Codex Security third-party, and leave the universal layer unchanged

## Context

The repository previously shipped a small advisory router under
`hooks/capability-router`. It could be refreshed by the public installer and
did not implement the current manifest, policy, full-task-input, dependency,
fallback, project-scope, receipt, and registry boundaries of the universal
router.

Security routing also needed to distinguish top-level scans from phase skills,
separate triage from validation and remediation, support all 13 Codex Security
skills, and compose the correct provider guidance for Supabase, Neon Postgres,
generic PostgreSQL, and frontend work.

Copying Codex-managed plugin skills into this repository would create a second
source, separate their code from their MCP and connector runtime, and make
updates and authentication ownership ambiguous.

## Decision

1. Delete the retired `hooks/capability-router` implementation and remove its
   installer refresh flags.
2. Keep a generic, public-safe port of the canonical router architecture under
   `capability-routing/` as dormant source and test material only.
3. Do not install, execute, register, or deploy the dormant router through the
   public package installer.
4. Keep the active universal router and all private project scope mappings
   outside this repository. Any universal deployment requires a separate
   explicit decision.
5. Record security routes in the repository policy with one primary and no
   more than two supports, explicit intent gates, provider qualifiers,
   dependency checks, bounded fallbacks, and authority limits.
6. Treat Codex Security, Supabase, and Neon Postgres as Codex-managed third-party
   plugins. Record their capabilities and integration requirements without
   copying their skill or runtime files.
7. Bundle provider-neutral local guidance, including
   `postgres-security-best-practices`, for work that must remain useful without
   a provider plugin.

## Security routing rules

- A Git-backed change set selects `codex-security:security-diff-scan`.
- An ordinary repository or scoped-path scan selects
  `codex-security:security-scan`.
- An explicitly exhaustive or multi-pass repository scan selects
  `codex-security:deep-security-scan`.
- Discovery, threat modeling, validation, attack-path analysis, fixing,
  hardening, policy definition, reporting, and tracking remain separate routes.
- Supabase routes use Supabase skills and its connector.
- Neon routes use Neon skills plus provider-neutral PostgreSQL guidance and
  never route through Supabase.
- Generic PostgreSQL routes use local PostgreSQL guidance and assume no
  provider connector.
- Frontend routes keep authorization, secrets, and privileged database actions
  outside the client.
- A request that spans frontend and provider security surfaces, more than one
  lifecycle phase, or more than one tracker destination fails closed and
  requires bounded tasks for each surface or phase.
- Bare, quoted, negated, or non-technical keyword matches do not activate a
  security or provider route.

## Dependency and fallback decision

Standard and diff scan fallback workflows retain their documented equivalent
single-pass behavior when the Codex Security MCP is unavailable. A deep scan
without that MCP is non-equivalent because it cannot reproduce independent
repeated discovery, semantic candidate reduction, or durable lifecycle
receipts.

Live Supabase and Neon validation requires a successful call to the intended
provider project. An enabled plugin or connector setting is not current-state
evidence. The route requires a successful dependency probe bound to the same
execution request. A missing, mismatched, authentication-failed, tool-failed,
or target-failed probe selects the declared fallback. An explicit static-only
or no-live instruction also overrides a callable probe. Static fallback is
non-equivalent and cannot prove deployed roles, grants, policies,
configuration, advisor state, or remote mutations.

## Alternatives rejected

- Keep and extend the retired hook. Rejected because it would preserve a second,
  weaker routing design.
- Install the canonical router automatically. Rejected because repository
  installation must not mutate the universal routing layer without separate
  authority.
- Copy the 13 Codex Security skills into `.agents/skills`. Rejected because the
  plugin owns those files and their runtime integration.
- Route all PostgreSQL work through Supabase. Rejected because Neon and generic
  PostgreSQL have different provider capabilities and operational boundaries.
- Route from provider names or security keywords alone. Rejected because it
  produces false positives and can select a write-capable integration for an
  unrelated task.

## Consequences

The repository now has one documented candidate routing architecture and a
complete provider-aware security decision map without claiming to own the
active universal deployment. Plugin updates remain Codex-managed. Offline and
static work remains possible through bundled guidance, with explicit disclosure
when it cannot reproduce live provider or deep-scan evidence.

The tradeoff is that installing this repository alone does not activate the new
router or install Codex Security, Supabase, or Neon Postgres. Operators must
install the needed plugins through Codex, and a separately authorized universal
change is required before the active router can use this repository candidate.

## Evidence test

Acceptance requires all of the following:

- no tracked Codex Security plugin skill or runtime files
- no public installer reference to the retired refresh flags or hook path
- a dormant reference router that cannot write universal state during tests
- explicit routes for all 13 Codex Security skills
- negative routing tests for bare, quoted, negated, and non-technical terms
- provider-specific Supabase and Neon compositions
- a provider-neutral PostgreSQL route
- explicit equivalent and non-equivalent fallback declarations
- documentation that distinguishes connector enablement from successful live
  verification

## Related sources

- `docs/security-capability-operating-model.md`
- `capability-routing/README.md`
- `capability-routing/routing-policy.yaml`
- `codex-capabilities/plugins.manifest.json`
- `codex-capabilities/tools.manifest.json`
