# Views And Security Invoker

## Choose The Boundary Deliberately

- PostgreSQL views use the view owner's base-relation privileges by default.
- Use `WITH (security_invoker = true)` when base relations and their RLS policies must be checked as the invoking user.
- Treat `security_barrier` as a separate property for views intended to enforce row filtering against user-supplied predicates.
- Use `WITH CHECK OPTION` on updatable views when writes must remain visible through the view predicate.
- Grant only the required operations on the view and review grants on its base relations.
- List columns explicitly for security-sensitive projections. Review new base-table columns so they cannot become exposed accidentally.
- Review functions called by the view independently. Function execution follows each function's invoker or definer setting.
- Confirm the supported PostgreSQL version implements every selected view option.

## Patterns

Invoker view:

```sql
CREATE VIEW api.order_summary
WITH (security_invoker = true)
AS
SELECT id, account_id, status, created_at
FROM app.orders;
```

Restricted updatable view:

```sql
CREATE VIEW api.open_orders
WITH (security_barrier = true)
AS
SELECT id, account_id, status
FROM app.orders
WHERE status = 'open'
WITH LOCAL CHECK OPTION;
```

These patterns solve different problems. Do not assume `security_invoker`, `security_barrier`, and `CHECK OPTION` are interchangeable.

## Review

```sql
SELECT n.nspname AS schema_name,
       c.relname AS view_name,
       pg_get_userbyid(c.relowner) AS owner,
       c.reloptions,
       pg_get_viewdef(c.oid, true) AS definition
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind = 'v'
ORDER BY schema_name, view_name;
```

## Proof

- Test the view as each runtime role, not only as its owner.
- Test direct access to every base relation.
- Test cross-scope predicates and functions that could leak filtered values.
- Test each allowed write and a write that violates the view predicate.
- Verify RLS behavior through the view and through any nested views.

Primary sources: [CREATE VIEW](https://www.postgresql.org/docs/current/sql-createview.html), [Rules and Privileges](https://www.postgresql.org/docs/current/rules-privileges.html), [Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).
