# Grants And Default Privileges

## Design

- Start from the minimum required privileges. Avoid `ALL PRIVILEGES` for application roles.
- Separate schema `USAGE` from schema `CREATE`. Runtime roles usually need `USAGE`, not `CREATE`.
- Review `PUBLIC` grants on databases, schemas, routines, tables, sequences, and types.
- Grant table actions independently: `SELECT`, `INSERT`, `UPDATE`, and `DELETE` are different capabilities.
- Grant column privileges when an operation genuinely needs only a stable subset of columns.
- Grant sequence privileges only when the write path uses the sequence.
- Revoke routine `EXECUTE` from `PUBLIC` when invocation is not intentionally public.
- Apply `ALTER DEFAULT PRIVILEGES` for the exact role that will create future objects. Defaults are based on the current object-creating role, not inherited role membership.
- Remember that default-privilege changes affect future objects only. Repair existing objects separately.

## Migration Pattern

Use exact names from the target system:

```sql
REVOKE CREATE ON SCHEMA app FROM PUBLIC;
GRANT USAGE ON SCHEMA app TO app_runtime;

REVOKE ALL ON ALL TABLES IN SCHEMA app FROM app_runtime;
GRANT SELECT, INSERT, UPDATE ON app.orders TO app_runtime;

ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA app
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA app
  GRANT SELECT ON TABLES TO app_reader;
```

Do not paste this pattern unchanged. Determine whether the application needs sequence, function, type, or additional table privileges.

## Review

```sql
SELECT grantee, table_schema, table_name, privilege_type, is_grantable
FROM information_schema.role_table_grants
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY grantee, table_schema, table_name, privilege_type;

SELECT defaclrole::regrole AS creator_role,
       defaclnamespace::regnamespace AS schema_name,
       defaclobjtype AS object_type,
       defaclacl AS acl
FROM pg_default_acl
ORDER BY creator_role::text, schema_name::text, object_type;
```

Use PostgreSQL privilege inquiry functions to prove effective privilege, including membership and `PUBLIC` paths:

```sql
SELECT has_schema_privilege('app_runtime', 'app', 'USAGE'),
       has_schema_privilege('app_runtime', 'app', 'CREATE'),
       has_table_privilege('app_runtime', 'app.orders', 'SELECT, UPDATE');
```

## Proof

- Verify both existing objects and a disposable future object created by the real migration owner.
- Test access through direct tables, views, routines, and sequences.
- Re-run catalog checks after the migration. Source SQL alone does not prove the deployed access control list.

Primary sources: [Privileges](https://www.postgresql.org/docs/current/ddl-priv.html), [ALTER DEFAULT PRIVILEGES](https://www.postgresql.org/docs/current/sql-alterdefaultprivileges.html), [Access Privilege Inquiry Functions](https://www.postgresql.org/docs/current/functions-info.html).
