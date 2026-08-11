# Security Regression Testing

## Build An Authorization Matrix

Define explicit fixtures for each role and operation:

| Role | Entry point | Operation | Scope | Expected |
|---|---|---|---|---|
| authorized runtime | table, view, or function | allowed action | own | success |
| authorized runtime | same entry point | adjacent action | own | denied |
| authorized runtime | same entry point | allowed action | other | denied or no rows |
| unauthorized runtime | same entry point | any protected action | any | denied |
| migration owner | migration path only | object change | controlled | success |

Include every exposed path. A function or view can have a different effective privilege path from its base table.

## Test Effective Privileges

Use catalog predicates for fast assertions:

```sql
SELECT has_schema_privilege('app_runtime', 'app', 'USAGE');
SELECT has_schema_privilege('app_runtime', 'app', 'CREATE');
SELECT has_table_privilege('app_runtime', 'app.orders', 'SELECT');
SELECT has_sequence_privilege('app_runtime', 'app.orders_id_seq', 'USAGE');
SELECT has_function_privilege(
  'app_runtime', 'api.cancel_order(uuid)', 'EXECUTE'
);
```

Then execute representative SQL as the actual login roles. Catalog predicates do not prove RLS expressions, function behavior, or state transitions.

## Required Negative Cases

- read another scope's row
- insert a row for another scope
- update the scope key or protected columns
- delete another scope's row
- call a privileged function without `EXECUTE`
- invoke a function with another scope's resource identifier
- access the base table directly when only a view or function should be exposed
- create an object in a trusted schema
- use an inherited or `SET ROLE` path to gain owner or administration rights
- reach a newly created object through unsafe default privileges

## Test Isolation

- Use disposable databases or transaction rollback where possible.
- Do not run destructive privilege tests against production without explicit authorization and a bounded rollback plan.
- Use dedicated test identities. Do not place real credentials in fixtures, logs, or output.
- Connect as the real role when login behavior or session settings matter. Document when `SET ROLE` is only an approximation.
- Assert both returned rows and durable state. A zero-row update and an authorization error can represent different contracts.

## Release Evidence

Record:

- PostgreSQL version and target environment
- migration checksum or exact commit
- role and object matrix tested
- catalog query results
- authorized and unauthorized execution results
- skipped paths and why
- remaining live-deployment verification

Primary sources: [Access Privilege Inquiry Functions](https://www.postgresql.org/docs/current/functions-info.html), [SET ROLE](https://www.postgresql.org/docs/current/sql-set-role.html), [Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).
