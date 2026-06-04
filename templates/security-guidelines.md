# Security Guidelines

## Security Decision
{{state_the_security_posture_for_first_release}}

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
- Do not log passwords, tokens, payment data, or private user content.
- Keep logs useful for debugging without becoming a second database.

## Prelaunch Gate
- Auth and authorization checked.
- Server validation checked.
- Secrets checked.
- Dependency audit checked.
- Backup or rollback path checked.
- Error states checked.

