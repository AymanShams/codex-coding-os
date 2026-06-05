# Documentation Stage Map

Use this reference with `technical-docs-pack` to decide which repo docs should be fully filled and which should remain not due.

## Rule

The technical documentation template defines the file structure. This stage map defines readiness. Do not fill late-stage docs with invented implementation details.

## Stages

| Stage | Name | Fill Standard |
|---|---|---|
| 0 | Idea and PRD | Fill from product decisions, user decisions, and source files |
| 1 | Product and workflows | Fill from PRD, app flow, user journeys, roles, and MVP boundaries |
| 2 | Architecture direction | Fill from tech stack, frontend/backend structure, security direction, and architecture decisions |
| 3 | Data and API design | Fill from entity model, API routes, auth model, integrations, TDD, and backend plan |
| 4 | Implementation | Fill only after code, migrations, CI, or concrete implementation exists |
| 5 | Operations | Fill only after deployment, monitoring, incidents, runbooks, support, or operating evidence exists |
| 6 | Maturity and scale | Fill only after usage, performance, reliability, cost, and scaling evidence exists |

## Required Early Documentation

For source-rich pre-build projects, fill Stage 0 to Stage 3 in full when the sources exist. At minimum, check:

- `docs/index.md`
- `docs/glossary.md`
- `docs/product/overview.md`
- `docs/product/requirements.md`
- `docs/product/non-functional-requirements.md`
- `docs/product/user-journeys.md`
- `docs/architecture/system-context.md`
- `docs/architecture/container-architecture.md`
- `docs/architecture/component-architecture.md`
- `docs/architecture/data-flow.md`
- `docs/architecture/tradeoffs.md`
- `docs/data/domain-model.md`
- `docs/data/schema.md`
- `docs/data/erd.md`
- `docs/api/overview.md`
- `docs/api/auth.md`
- `docs/api/openapi.yml`
- `docs/api/error-model.md`
- `docs/api/rate-limits.md`
- `docs/frontend/overview.md`
- `docs/frontend/routing.md`
- `docs/frontend/state-management.md`
- `docs/backend/services.md`
- `docs/backend/configs.md`
- `docs/integrations/overview.md`
- `docs/security/threat-model.md`
- `docs/security/iam-rbac.md`
- `docs/security/secrets-key-management.md`
- `docs/security/encryption.md`
- `docs/security/audit-logging.md`
- `docs/security/privacy.md`
- `docs/testing/strategy.md`
- `docs/testing/test-data.md`
- `docs/delivery/environments.md`
- `docs/performance/targets.md`
- `docs/performance/scalability-roadmap.md`
- `docs/edge-cases/catalog.md`
- `docs/edge-cases/error-handling.md`

Tailor this list to the template and project. Do not treat it as a fixed universal count.

## Not Due Wording

For Stage 4 to Stage 6 files before implementation, write a short controlled placeholder:

```md
# <Document Name>

## Status
Not due yet. This document depends on implementation, deployment, operating, or measured evidence that does not exist yet.

## Current Source Of Truth
- <link to controlling planning docs>

## Trigger To Fill
- Fill this document when <specific implementation or operational event occurs>.
```

Avoid `TODO`, `TBD`, and vague placeholders in repo docs.
