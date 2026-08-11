# Security Capability Operating Model

## Decision

Codex Coding OS uses one explicit security route for each task. The route has
one primary workflow owner and no more than two supporting capabilities.

The repository bundles provider-neutral security guidance. Codex manages the
Codex Security, Supabase, and Neon Postgres plugins. Their skill source,
connectors, credentials, and runtime state are never copied into this
repository.

The router source under `capability-routing/` is a dormant, public-safe
reference implementation. It is tested as repository source but is not
installed, registered as a hook, or allowed to mutate the universal Codex
router. Activating it outside this repository requires a separate explicit
decision and a reviewed deployment.

## Routing conditions

A dedicated security scan, finding, policy, or hardening workflow requires
both:

1. An explicit security action such as scan, review, triage, validate, fix,
   threat model, harden, or track.
2. Concrete technical context such as a repository, diff, endpoint,
   authorization path, database object, migration, Supabase project, Neon
   project, or frontend security boundary.

The router does not activate security from the word `security` alone, a quoted
example, a negated instruction such as `do not scan`, a provider name used as
branding, or a generic word such as `policy` or `view` without technical
context.

Routing selects guidance. It does not grant permission to edit code, mutate a
database, call a provider, publish a report, or create an external issue.

### When security guidance is automatic support

Secure-by-default guidance is added as support during implementation or review
when the actual changed surface contains a security boundary, including:

- authentication, sessions, cookies, or tokens
- authorization, roles, permissions, or admin actions
- secrets or privileged provider credentials
- public endpoints, uploads, or expensive unauthenticated actions
- database roles, ownership, grants, row-level security, views, or privileged
  functions
- Supabase Auth, Storage, exposed schemas, service-role use, or migrations
- Neon Auth, Data API, roles, branches, or migrations
- frontend cross-site scripting, content security policy, token storage, or
  server-bound authorization

This support does not silently convert ordinary implementation into a Codex
Security scan. A scan still requires the explicit scan intent and the correct
diff, standard, or deep selector.

## Codex Security plugin boundary

Compatibility is recorded against `codex-security@openai-curated-remote`
version `0.1.18`. Install and update it through Codex. Do not vendor its 13
skills, MCP implementation, caches, authentication state, or receipts.

| Plugin skill | Use it when | Do not use it as |
|---|---|---|
| `codex-security:security-diff-scan` | Reviewing a pull request, commit, branch diff, working-tree diff, or other Git-backed change set | A full repository scan |
| `codex-security:security-scan` | Running the default single-pass scan of a repository or scoped path when no diff is the subject | A diff scan or exhaustive repeated scan |
| `codex-security:deep-security-scan` | The user explicitly requests a deep, exhaustive, multi-pass repository or scoped-path scan | A pull request, commit, branch, or working-tree review |
| `codex-security:finding-discovery` | Discovering candidate findings in a bounded scope or executing the discovery phase of a scan | Confirmation, severity proof, remediation, or a complete scan workflow |
| `codex-security:threat-model` | Creating or updating the Codex Security scan threat-model artifact, or when the user explicitly asks for the plugin threat model | A standalone architecture review that does not need the plugin scan lifecycle |
| `codex-security:triage-finding` | Assessing imported alerts, reports, advisories, or backlog items for likely repository impact and missing evidence | Discovery, validation, fixing, or duplicate bug triage |
| `codex-security:validation` | Confirming or disproving one or more candidate findings with evidence | A complete repository scan or authority to fix a finding |
| `codex-security:attack-path-analysis` | Tracing a finding from entry point to reachable operation and calibrating exploitability and severity | A full scan or an instruction to exploit an external system |
| `codex-security:fix-finding` | The user explicitly asks to remediate a validated or plausible finding and verify the fix | Automatic remediation after discovery or triage |
| `codex-security:propose-security-hardening` | Designing structural improvements beyond one patch, comparing alternatives, or creating an implementation-ready hardening plan | Permission to implement the proposal |
| `codex-security:define-security-policy` | Creating or updating technical `SECURITY.md` review scope, required security properties, or exclusions | Code changes, provider mutation, or external publication |
| `codex-security:vulnerability-writeup` | Turning validated notes, proof, code evidence, or scan findings into a self-contained vulnerability report | Validation by itself |
| `codex-security:track-findings` | Creating an approved issue, ticket, or draft advisory for one finding or an explicitly selected batch | A scan, fix, or unapproved external write |

### Scan selection

Use this order before choosing any phase skill:

| Requested surface | Primary skill |
|---|---|
| Git-backed change set | `codex-security:security-diff-scan` |
| Repository or scoped path, ordinary depth | `codex-security:security-scan` |
| Repository or scoped path, explicitly exhaustive or multi-pass | `codex-security:deep-security-scan` |
| Existing alert or report | `codex-security:triage-finding` |
| Candidate that needs proof | `codex-security:validation` |
| Validated finding that needs a requested repair | `codex-security:fix-finding` |

`finding-discovery`, `attack-path-analysis`, and the plugin `threat-model` are
phase capabilities. They do not replace the top-level scan selector.

## Repository-owned security skills

These skills are bundled because they provide durable, provider-neutral
guidance without depending on a third-party plugin runtime.

| Local skill | Use it when | Relationship to Codex Security |
|---|---|---|
| `security-best-practices` | Language or framework secure defaults, focused code guidance, passive detection of major mistakes, or a secure-code review | Supports scans and fixes. It is not the owner of a requested Codex Security scan when that plugin is available |
| `security-threat-model` | A standalone repository, feature, service, or architecture threat model with assets, trust boundaries, entry points, attacker capabilities, and abuse paths | Owns standalone architecture threat modeling. The plugin threat-model skill owns the Codex Security scan artifact |
| `security-ownership-map` | Security-oriented ownership, bus factor, orphaned sensitive code, ownership hotspots, or CODEOWNERS reality checks from Git history | Separate analysis route. It does not discover or validate vulnerabilities |
| `defensive-security-checklist` | A bounded defensive checklist for code, dependencies, cloud surfaces, CI, MCPs, agents, or incident preparedness | Produces a non-executing checklist. It is not a broad scan or exploit workflow |
| `postgres-security-best-practices` | Provider-neutral PostgreSQL roles, ownership, grants, default privileges, row-level security, views, privileged functions, and privilege regression tests | Supports Neon and generic PostgreSQL work. It does not replace provider operations or application authentication review |

The parity receipt at `.agents/security-skill-parity.json` records the reviewed
relationship between repository-owned skills and the corresponding universal
copies. Repository changes do not update the universal layer.

## Provider composition

Provider selection is based on the actual technical surface. A database vendor
name never activates the wrong provider workflow.

| Project surface | Primary provider guidance | Security composition | Live integration |
|---|---|---|---|
| Supabase | `supabase:supabase` and `supabase:supabase-postgres-best-practices` | Add the selected Codex Security workflow for scans, triage, validation, attack-path analysis, fixes, or hardening | Supabase plugin app connector for current project evidence and approved operations |
| Neon Postgres | `neon-postgres:neon-postgres` plus local `postgres-security-best-practices` | Add the selected Codex Security workflow. Use `neon-postgres:neon-postgres-egress-optimizer` only for egress or payload work | Neon Postgres plugin app connector. A separately configured `neon` MCP is optional, not assumed |
| Generic PostgreSQL | Local `postgres-security-best-practices` plus `security-best-practices` when application code is involved | Add the selected Codex Security workflow for broad or diff scans | No provider connector is assumed |
| Frontend | `security-best-practices` with the actual framework guidance | Add Codex Security only for an explicitly requested scan or finding workflow. Split a task that also reaches a provider auth or data boundary into separate frontend and provider routes | Browser or provider tools are evidence sources only |

### Supabase

Use Supabase capabilities for Supabase projects, migrations, SQL, Auth,
Storage, row-level security, database functions, and provider operations. For a
security scan, the Codex Security skill remains primary and the two Supabase
skills supply provider context.

Do not use the generic PostgreSQL skill to replace Supabase-specific behavior
when the issue depends on Supabase Auth, Storage, exposed schemas, service
roles, project settings, or the provider's advisor output.

### Neon Postgres

Use Neon capabilities for Neon projects, branches, databases, Data API, Auth,
migrations, and live provider operations. Pair them with
`postgres-security-best-practices` for roles, grants, ownership, row-level
security, views, and privileged functions.

Neon never falls through to Supabase. If Neon is the provider, no Supabase
skill, connector, rule, or assumption is selected.

### Generic PostgreSQL

Use the local PostgreSQL skill when the provider is absent, irrelevant, or not
supported. Validate effective privileges rather than inferring access from SQL
text alone. Test both allowed and denied paths for roles, row-level security,
views, and privileged functions.

### Frontend and full-stack work

The frontend may enforce display and interaction rules, but it cannot be the
authoritative security boundary. Keep protected actions, authorization,
database access, secret use, and privileged provider calls on a trusted server
or database boundary. Validate inputs at that boundary and test unauthorized
paths.

When one request spans a frontend plus Supabase, Neon, or generic PostgreSQL,
the router fails closed and requires two bounded tasks. One task owns the
provider or server boundary. The other owns the frontend boundary. This split
prevents ordered routing from silently dropping either surface while keeping
each route within the two-support limit. Provider choice changes the database
and connector support. It does not change the rule that client code cannot
hold privileged credentials or make final authorization decisions.

## Evidence and fallback rules

### Codex Security MCP

The Codex Security MCP is required for its full deep-scan behavior.

| Workflow | Fallback when the MCP is unavailable | Equivalence |
|---|---|---|
| Diff scan | Bounded terminal and chat diff workflow using `codex-security:security-diff-scan` | Equivalent for the documented diff workflow |
| Standard scan | Prompt-only single-pass workflow using `codex-security:security-scan` | Equivalent for the documented standard workflow |
| Deep scan | At most three bounded Codex review passes using local guidance | Non-equivalent |

The deep fallback does not reproduce independent repeated discovery, semantic
candidate reduction, or durable deep-scan lifecycle receipts. Report that gap
instead of claiming a Codex Security deep scan completed.

### Provider connectors

An enabled connector is not proof that authentication works, the target project
is correct, or a live call succeeded. A callable route requires a successful
`live_call` probe for the exact dependency, bound to the same
`execution_request_id` as the complete task input. The normalized routing
prompt, task instruction, and optional bounded task text must match so a probe
cannot be rebound to another request. A missing or mismatched probe, or an
authentication, tool, or target failure, selects the declared
fallback. An explicit static-only or no-live instruction also selects the
fallback even when a callable probe exists. Current-state claims require a
successful read from the intended project and evidence tied to that read.

When the Supabase or Neon connector is unavailable:

- static source and migration review may continue
- provider mutation is not attempted
- live validation is non-equivalent
- deployed roles, grants, policies, settings, and advisor state remain
  unconfirmed
- the result must state exactly which live surface was not verified

Ordinary provider operations also fall back to static guidance only. Static
guidance cannot create branches, query a remote database, change settings, or
prove deployed state.

## Finding lifecycle and authority

Use these stages without collapsing one into another:

1. Discover candidates.
2. Triage imported material when needed.
3. Validate or disprove each candidate.
4. Analyze the reachable attack path and calibrate impact.
5. Write the report or propose structural hardening.
6. Fix only when the user explicitly requests remediation.
7. Re-run focused validation and the relevant diff checks.
8. Track externally only after destination, payload, and write approval are
   explicit.

One route owns one affirmative lifecycle phase. A request that combines scan,
discovery, validation, remediation, reporting, or tracking phases fails closed
and must be split into ordered tasks. This prevents an early evidence phase
from being silently skipped in favor of a later mutation phase.

A candidate is not a confirmed finding. A local fix is not proof of deployed
state. A successful provider read is not permission to write. A generated
report is not permission to publish.

For every confirmed finding, record the entry point, missing or bypassable
control, reachable operation, impact, counterevidence or proof gap,
disposition, and verification step.

## External finding tracking

`codex-security:track-findings` can use GitHub, Linear, or Jira integrations
when available. Every write follows the same sequence:

1. Validate the sealed finding source.
2. Check for an existing record.
3. Show the exact destination and payload.
4. Obtain explicit approval for that write.
5. Create or update the record.
6. Read it back and report the resulting identifier.

One route accepts one affirmative tracker destination. Explicitly negated
destinations are excluded. Requests that name more than one affirmative
destination fail closed and must be split, so ordered routing cannot publish to
only the first tracker and silently drop the rest.

The repository stores no connector credentials or plugin state.

## Installation and verification

1. Install Codex Coding OS from the reviewed repository release.
2. Open Codex Plugins and install Codex Security.
3. Install Supabase only for Supabase projects.
4. Install Neon Postgres only for Neon projects.
5. Connect only the provider used by the current project.
6. Restart Codex and open a new task after plugin changes.
7. Confirm the expected skills are visible before relying on automatic routing.
8. For live work, make a bounded read and verify the project identity before
   any current-state claim.

The machine-readable plugin boundary is in
`codex-capabilities/plugins.manifest.json`. Connector and MCP expectations are
in `codex-capabilities/tools.manifest.json`. Repository routing decisions are
in `capability-routing/routing-policy.yaml`.

## Migration from the retired router

The former `hooks/capability-router` package and its installer-driven refresh
path are retired. Do not call `-RefreshCapabilityIndex`,
`--refresh-capability-index`, or any script under that deleted path.

The replacement is intentionally split:

- `capability-routing/` stores dormant, generic reference source and tests
- the active universal router remains outside this repository
- the public installer does not refresh or replace universal routing state
- Codex-managed plugins remain external and are installed through Codex

This split prevents a repository installation from silently changing global
routing, private project mappings, plugin state, or credentials.
