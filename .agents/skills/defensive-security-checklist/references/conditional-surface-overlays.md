# Conditional Surface Overlays

Use an overlay only when the matching surface is evidenced in scope. Do not infer a surface from generic framework or provider names. Record the evidence that activated it.

## Costly Or Metered Operations

- Identify operations that consume billable or scarce resources.
- Enforce per-actor and per-scope limits before dispatch. Bound retries, concurrency, payload size, and work size.
- Record limit behavior, failure behavior, and cost-monitoring evidence.

## Storage

- Verify that the authoritative access path enforces actor, resource, scope, operation, and expiry where applicable.
- Verify that upload, download, sharing, export, and deletion flows constrain object ownership, content type, size, and lifecycle.
- Request storage-policy, access-code, signed-link, and negative-path test evidence.

## Credential Detection And Response

- Use detection appropriate to source, build, log, and release paths.
- When exposure is suspected or confirmed, revoke or rotate the credential, identify reachable use, and verify the replacement path. Redaction alone is not remediation.
- Record detection output, affected scope, rotation evidence, and verification result.

## Payment Flows

- Keep amount, product, entitlement, and state transitions authoritative at the server or provider-verified boundary.
- Verify webhook authenticity and replay handling before state changes.
- Exercise valid, invalid, duplicate, and out-of-order events when the integration exposes them.

## Recovery Evidence

- Require evidence that a restore produces a usable service or data set, not only that a backup artifact exists.
- Verify recovery scope, restore procedure, access control, integrity checks, and result against the stated recovery objective.
- Record date, environment, evidence, gaps, and owner.

## Managed Platforms

- Inspect actual deployed configuration and current platform documentation. Do not assume provider defaults or marketing claims.
- Verify public exposure, identity and role settings, storage or database policy, network or egress controls, credential handling, logs, and rollback behavior that the platform supports.
- Record provider, environment, configuration evidence, and assumptions.
