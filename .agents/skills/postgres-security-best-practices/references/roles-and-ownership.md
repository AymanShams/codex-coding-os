# Roles And Ownership

## Design

- Separate identities by purpose: non-login owner roles, migration or deployment roles, runtime login roles, read-only support roles, and break-glass administration.
- Make ordinary runtime roles `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`.
- Grant application privileges through narrowly scoped group roles. Review inherited and `SET ROLE` paths, not only direct grants.
- Keep object owners out of normal application connections. Owners can alter or drop their objects and normally bypass row-level security.
- Own security-sensitive schemas, tables, views, and routines with controlled non-login roles.
- Avoid granting role membership with administration or delegation rights unless that delegation is an explicit requirement.
- Treat predefined roles as privileged capabilities whose exact permissions can change between PostgreSQL releases.

## Review

Inspect role attributes and memberships:

```sql
SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
       rolreplication, rolbypassrls
FROM pg_roles
ORDER BY rolname;

SELECT granted.rolname AS granted_role,
       member.rolname AS member_role,
       membership.admin_option,
       membership.inherit_option,
       membership.set_option
FROM pg_auth_members AS membership
JOIN pg_roles AS granted ON granted.oid = membership.roleid
JOIN pg_roles AS member ON member.oid = membership.member
ORDER BY granted_role, member_role;
```

Confirm object ownership separately:

```sql
SELECT n.nspname AS schema_name, c.relname AS object_name,
       c.relkind, pg_get_userbyid(c.relowner) AS owner
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY schema_name, object_name;
```

On PostgreSQL releases before membership-level `inherit_option` and `set_option`, use the catalog shape supported by that release. Never silently drop the membership-path check.

## Proof

- Connect as the real runtime login when possible. `SET ROLE` is useful but does not reproduce every login or session configuration behavior.
- Verify that runtime credentials cannot become an owner, migration, or administration role.
- Verify owner-only operations fail for runtime roles.
- When row-level security is relied upon, verify the executing role is not the table owner, a superuser, or a `BYPASSRLS` role.

Primary sources: [Database Roles](https://www.postgresql.org/docs/current/user-manag.html), [Role Membership](https://www.postgresql.org/docs/current/role-membership.html), [Predefined Roles](https://www.postgresql.org/docs/current/predefined-roles.html).
