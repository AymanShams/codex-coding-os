# Security Definer Functions

## Default To Invoker Rights

Use `SECURITY INVOKER` unless a narrowly defined operation must cross a privilege boundary. A `SECURITY DEFINER` function executes with its owner's privileges and must be treated as a capability endpoint.

For every definer function:

- Give ownership to a controlled non-login role with only the privileges the function needs.
- Use a fixed `search_path` containing only trusted schemas, with `pg_temp` last.
- Schema-qualify referenced tables, functions, operators, and types where practical.
- Validate actor, action, resource, allowed fields, parameters, and current state inside the authoritative function boundary.
- Avoid dynamic SQL. If it is unavoidable, use `format()` with `%I` for identifiers and `%L` or parameters for values.
- Revoke `EXECUTE` from `PUBLIC` and grant it only to intended roles, in the same transaction as creation.
- Review overloading and grant the exact routine signature.
- Do not assume RLS protects operations executed as a table owner, superuser, or `BYPASSRLS` role.
- Keep the function small and return only the minimum required data.

## Pattern

```sql
BEGIN;

CREATE OR REPLACE FUNCTION api.cancel_order(p_order_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = api, app, pg_temp
AS $$
BEGIN
  UPDATE app.orders
  SET status = 'cancelled'
  WHERE id = p_order_id
    AND account_id = api.current_account_id()
    AND status = 'open';

  IF NOT FOUND THEN
    RAISE EXCEPTION 'order cannot be cancelled';
  END IF;
END;
$$;

REVOKE ALL ON FUNCTION api.cancel_order(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION api.cancel_order(uuid) TO app_runtime;

COMMIT;
```

This is a shape, not a complete authorization implementation. Prove that `current_account_id()` is trusted and that the owner role cannot reach unrelated objects.

## Review

```sql
SELECT n.nspname AS schema_name, p.proname,
       pg_get_function_identity_arguments(p.oid) AS arguments,
       pg_get_userbyid(p.proowner) AS owner,
       p.prosecdef AS security_definer,
       p.proconfig AS function_settings,
       p.proacl AS acl
FROM pg_proc AS p
JOIN pg_namespace AS n ON n.oid = p.pronamespace
WHERE p.prosecdef
ORDER BY schema_name, p.proname, arguments;
```

Verify effective execution with `has_function_privilege` and actual calls from authorized and unauthorized roles.

Primary sources: [CREATE FUNCTION](https://www.postgresql.org/docs/current/sql-createfunction.html), [Function Security](https://www.postgresql.org/docs/current/perm-functions.html), [Function Information](https://www.postgresql.org/docs/current/functions-info.html).
