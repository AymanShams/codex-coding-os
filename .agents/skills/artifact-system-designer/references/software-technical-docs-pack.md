# Software Technical Documentation Pack Reference

Use this reference when an artifact system is specifically a software-repo documentation system. If the user asks to draft or fill the repo docs themselves, prefer the `technical-docs-pack` skill.

## When This Pattern Applies

- A repo needs a `/docs` structure for developer onboarding, AI-assisted coding, production handoff, or operational continuity.
- The user wants source-of-truth rules for API docs, data docs, architecture decisions, runbooks, and release documentation.
- The artifact system must prevent documentation rot through owners, PR checks, review cadence, and CI gates.

## Pattern Summary

The software docs system should separate:

1. Product truth: overview, users, requirements, non-functional requirements, metrics.
2. Architecture truth: system context, containers, components, data flow, tradeoffs, ADRs.
3. Data truth: schema, migrations, ERD, retention, backup and restore rules.
4. API truth: OpenAPI, auth, errors, versioning, examples.
5. Operational truth: SLOs, monitoring, logs, alerts, runbooks, incidents, postmortems.
6. Security truth: threat model, RBAC, secrets, encryption, audit logs, compliance mapping.
7. Delivery truth: environments, CI, CD, deployments, rollback, database change safety.

## Governance Requirements

- Add explicit owner mapping for documentation areas where needed.
- Add a PR Definition of Done that requires doc updates when API, database, config, behavior, security controls, or architecture decisions change.
- Review documentation drift weekly or per release.
- Review operational truth monthly, especially alerts, runbooks, and restore proof.
- Treat decks and summaries as derived views unless explicitly approved as source-of-truth.

## Routing

- Use `technical-docs-pack` for creating or filling the file-by-file repo documentation pack.
- Use `artifact-system-designer` when the repo docs are one part of a wider company artifact library, evidence room, governance system, or cross-project documentation operating model.
