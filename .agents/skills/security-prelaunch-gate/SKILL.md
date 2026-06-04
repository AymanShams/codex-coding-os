---
name: security-prelaunch-gate
description: Use when creating or reviewing apps that include auth, public endpoints, forms, databases, user data, admin features, payments, exports, uploads, AI features, or pre-launch safety checks.
---

# Security Prelaunch Gate

Use this skill before launch or before calling a feature safe.

## Minimum Viable Security Checklist

Check:

- authentication and session hardening
- authorization and object-level access
- server-side input validation
- rate limits and quotas
- bot friction where needed
- secrets kept out of frontend code and logs
- environment variables placeholder-only in examples
- CORS kept tight but not treated as real API security
- dependency and lockfile review
- safe error messages
- logging and audit for sensitive actions
- backups and rollback plan
- migration safety
- file upload restrictions
- export restrictions

## High-Risk Areas

Treat these as high-risk:

- login
- password reset
- admin panels
- billing or payments
- file uploads
- data exports
- webhooks
- background jobs
- AI-generated user actions
- database migrations
- permissions and roles

## Review Method

1. Identify stack and data sensitivity.
2. Find auth and authorization boundaries.
3. Inspect public routes and mutation endpoints.
4. Inspect validation logic.
5. Inspect secret handling.
6. Inspect logs and error messages.
7. Inspect tests for negative cases.
8. Report severity and fixes.

## Default Recommendations

- Deny by default.
- Validate on the server.
- Authorize by object, not only by role.
- Rate-limit auth and public forms.
- Use least privilege API keys.
- Keep service keys server-only.
- Use fake test data.
- Do not expose stack traces to users.

## Output

Report:

- blocking security issues
- non-blocking improvements
- checks run
- checks not run
- launch readiness verdict

