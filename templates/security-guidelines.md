# Security Guidelines

## Security Decision
{{state_the_security_posture_for_first_release}}

## Security Capability Route

| Decision | Selection |
|---|---|
| Review surface | Git diff, repository, scoped path, existing finding, or architecture |
| Primary workflow | Diff scan, standard scan, deep scan, triage, validation, fix, or threat model |
| Provider | Supabase, Neon Postgres, generic PostgreSQL, or none |
| Live evidence required | Yes or No |
| Non-equivalent fallback | {{state_what_cannot_be_proven_without_the_live_dependency}} |

## Data Classification
| Data | Sensitivity | Storage | Access |
|---|---|---|---|
| {{data}} | Low, Medium, High | {{storage}} | {{access}} |

## Authentication
- Use a proven authentication provider or framework.
- Never build password storage from scratch.
- Require secure session handling.
- Define logout and account recovery behavior.

## Authorization
- Check permissions on the server for every protected action.
- Do not rely on hidden frontend buttons for security.
- Add role-based rules where roles exist.

## Provider and Database Boundary

- For Supabase, review Supabase Auth, exposed schemas, row-level security,
  Storage, database functions, service-role use, and project settings with the
  Supabase plugin when live evidence is required.
- For Neon Postgres, review Neon project and branch behavior with the Neon
  Postgres plugin, then apply provider-neutral PostgreSQL checks for roles,
  ownership, grants, row-level security, views, and privileged functions.
- For generic PostgreSQL, assume no provider connector. Prove effective access
  with role and privilege tests.
- Never use Supabase assumptions for Neon or generic PostgreSQL.
- Treat connector enablement as configuration, not proof that authentication,
  target identity, or a live operation succeeded.

## Frontend Boundary

- Keep privileged credentials and provider administration outside client code.
- Treat frontend route guards and hidden controls as user experience only.
- Enforce actor, action, resource, writable fields, and required state at the
  authoritative server or database boundary.
- Test unauthorized paths as well as successful paths.

## Input Validation
- Validate request bodies on the server.
- Reject unexpected fields.
- Sanitize data before rendering user-generated content.
- Use parameterized database queries or a safe query builder.

## Secrets
- Store secrets in environment variables or the deployment platform secret manager.
- Never commit `.env` files with real values.
- Add `.env.example` with placeholder values only.

## Abuse Protection
- Add rate limiting for login, public forms, file uploads, and expensive actions.
- Restrict CORS to expected origins when public APIs exist.
- Limit file type and file size for uploads.

## Logging
- Log failures and important admin actions.
- Do not log passwords, tokens, payment data, or sensitive user content.
- Keep logs useful for debugging without becoming a second database.

## Prelaunch Gate
- Auth and authorization checked.
- Server validation checked.
- Secrets checked.
- Dependency audit checked.
- Backup or rollback path checked.
- Error states checked.
- Database roles, grants, row-level security, views, and privileged functions
  tested where PostgreSQL is used.
- Live provider claims tied to a successful read from the intended project.
- Static or unavailable-provider gaps stated without claiming live validation.
