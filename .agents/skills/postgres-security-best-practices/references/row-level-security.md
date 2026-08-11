# Row-Level Security

## Model

Row-level security (RLS) filters rows after normal object privileges permit the operation. It does not grant table access by itself.

- Enable RLS on every table whose rows are scoped by actor, account, tenant, or ownership.
- Use `FORCE ROW LEVEL SECURITY` when the table owner must also be subject to policies.
- Keep ordinary runtime sessions away from table-owner, superuser, and `BYPASSRLS` roles.
- Treat a table with RLS enabled and no applicable policy as default deny.
- Scope policies by command and role. Keep read visibility in `USING` and proposed-row validity in `WITH CHECK`.
- Remember that permissive policies combine with `OR` and restrictive policies combine with `AND`.
- Keep policy expressions small, stable, schema-qualified, and based on trusted context.
- Do not trust a session setting that the caller can freely change. Establish identity context at an authoritative server boundary and test spoofing attempts.

## Pattern

```sql
ALTER TABLE app.orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.orders FORCE ROW LEVEL SECURITY;

CREATE POLICY orders_read_own
ON app.orders
FOR SELECT
TO app_runtime
USING (account_id = app.current_account_id());

CREATE POLICY orders_write_own
ON app.orders
FOR INSERT
TO app_runtime
WITH CHECK (account_id = app.current_account_id());
```

The identity function in this example is itself part of the authorization boundary and must be reviewed. Do not derive authority from an unchecked client parameter.

## Review

```sql
SELECT n.nspname AS schema_name, c.relname AS table_name,
       c.relrowsecurity AS rls_enabled,
       c.relforcerowsecurity AS rls_forced,
       pg_get_userbyid(c.relowner) AS owner
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY schema_name, table_name;

SELECT schemaname, tablename, policyname, permissive, roles, cmd,
       qual, with_check
FROM pg_policies
ORDER BY schemaname, tablename, policyname;
```

## Proof Matrix

For each command, prove:

- authorized same-scope row succeeds
- unauthorized cross-scope row is invisible or rejected
- inserts and updates cannot move a row into another scope
- deletes cannot target another scope
- bulk statements, joins, views, and functions preserve the same boundary
- owner, migration, and break-glass paths are absent from ordinary runtime execution

Primary sources: [Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html), [CREATE POLICY](https://www.postgresql.org/docs/current/sql-createpolicy.html), [pg_policies](https://www.postgresql.org/docs/current/view-pg-policies.html).
