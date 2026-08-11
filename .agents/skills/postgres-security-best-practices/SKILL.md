---
name: postgres-security-best-practices
description: Use when designing, reviewing, or hardening provider-neutral PostgreSQL authorization boundaries, including roles and ownership, grants and default privileges, row-level security, views, SECURITY DEFINER functions, and privilege regression tests. Use for PostgreSQL schema or migration work where effective database access must be proven. Do not use as a substitute for provider-specific platform configuration, application-layer authentication review, a broad security scan, or a repository-wide threat model.
---

# PostgreSQL Security Best Practices

Secure the database as an independent authorization boundary. Application checks, migration text, and intended grants are not proof of effective access.

## Workflow

1. Establish the supported PostgreSQL version, deployment environment, entry points, runtime roles, migration role, and object owners.
2. Begin with read-only catalog and privilege inspection. Do not change a live database unless the user has authorized the exact target and mutation.
3. Trace each protected operation as `actor -> role membership -> schema -> object -> row or function boundary -> state change`.
4. Record the entry point, missing or bypassable control, reachable operation, impact, counterevidence or proof gap, disposition, and verification step for each finding.
5. Design the smallest privilege correction. Keep ownership, routine execution, table privileges, and row filtering as separate controls.
6. Verify effective behavior as every relevant authorized and unauthorized role. Test direct table access and all exposed views and functions.

## Load References Selectively

- Read `references/roles-and-ownership.md` for login roles, group roles, object owners, memberships, and privileged role attributes.
- Read `references/grants-and-default-privileges.md` for schemas, tables, sequences, routines, existing grants, and future-object defaults.
- Read `references/row-level-security.md` for tenant or actor-scoped row access and write checks.
- Read `references/views-and-security-invoker.md` for views, base-table privilege behavior, row filtering, and updatable-view checks.
- Read `references/security-definer-functions.md` before creating or reviewing any privileged routine.
- Read `references/security-regression-testing.md` for an executable authorization test matrix and release evidence.

Load every reference that matches the changed surface. A change touching a privileged function over an RLS table requires both function and RLS guidance.

## Required Security Properties

- Use non-login owner roles and narrowly privileged runtime roles.
- Keep runtime roles free of `SUPERUSER`, `CREATEDB`, `CREATEROLE`, `REPLICATION`, and `BYPASSRLS` unless the proven operational requirement cannot be met otherwise.
- Treat schema `CREATE`, object ownership, role membership, grant options, default privileges, and `PUBLIC` privileges as escalation paths.
- Treat row-level security as an additional filter, not a replacement for object privileges.
- Prefer `SECURITY INVOKER`. Make each `SECURITY DEFINER` routine a small, reviewed capability with a fixed safe `search_path` and explicit `EXECUTE` grants.
- Never rely on hidden client conventions, editable session context, or application-only filtering as database authorization.
- Make migrations idempotent where the repository requires it, but never let idempotence hide an incorrect effective privilege state.

## Verification Contract

For every protected operation, prove all of the following:

- the intended role can perform the intended action
- the role cannot perform adjacent unauthorized actions
- a different scope, tenant, or owner cannot read or mutate the protected row
- direct table, view, function, and sequence paths have the intended behavior
- object owners, superusers, and `BYPASSRLS` roles are excluded from ordinary runtime paths
- default privileges create the same safe posture for new objects
- catalog predicates such as `has_schema_privilege`, `has_table_privilege`, `has_sequence_privilege`, and `has_function_privilege` match executed behavior

If a live target is unavailable, state that deployment effectiveness remains unverified. Passing SQL lint or migration tests proves only source behavior.

## Output

Return a prioritized finding or change summary with exact objects, roles, evidence, expected behavior, executed checks, unexecuted checks, and residual proof gaps. Keep provider-specific platform recommendations separate from PostgreSQL controls.
