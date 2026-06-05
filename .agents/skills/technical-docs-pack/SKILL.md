---
name: technical-docs-pack
description: Use when the user asks to create, upgrade, structure, fill, audit, or maintain a complete technical documentation pack for a software repo, app, platform, module, or engineering handoff. Use for repo documentation systems covering README, product overview, requirements, non-functional requirements, architecture, data, API, frontend, backend, integrations, security, reliability, runbooks, delivery, testing, performance, edge cases, ADRs, CODEOWNERS, PR documentation checks, source-of-truth rules, and documentation rot prevention. Use when the goal is developer onboarding, AI-navigable repo context, production handoff, operational readiness, or "make sure a new developer can continue building." Do not use for a standalone PRD only, product strategy only, customer-facing help docs only, one routine SOP only, or code architecture refactoring without documentation output. If the primary task is a PRD, use create-prd. If the primary task is designing a broad artifact library or governance system beyond a software repo, use artifact-system-designer. If the primary task is validating readiness of existing documents, use artifact-validation-workflow.
---

# Technical Docs Pack

Use this skill to create a complete software-repo documentation pack that helps a new developer run, understand, change, operate, and safely extend a system without relying on tribal knowledge.

The goal is not a large folder tree. The goal is a living documentation system tied to code, source-of-truth files, ownership, review cadence, and pull request discipline.

## Use When

- The user wants a full technical documentation template for an app, repo, platform, module, or codebase.
- The user wants documentation that supports developer handoff, onboarding, AI-assisted coding, production operations, or continuity after a developer leaves.
- The user asks for `/docs` structure, architecture docs, API docs, data docs, security docs, runbooks, ADRs, testing docs, deployment docs, or documentation governance.
- The user asks to upgrade a PRD or technical spec into a complete engineering documentation system.
- The user asks what documentation must exist so future development does not restart from scratch.

## Do Not Use When

- The user only needs a Product Requirements Document. Use `create-prd`.
- The user needs a customer-facing help center article, FAQ, API reference article, or support guide. Use `support-docs`.
- The user needs one internal process, SOP, onboarding playbook, or incident runbook. Use `process-docs` or `ssot-drafter` depending on stakes.
- The user needs a broad non-software artifact library, governance folder, evidence room, or knowledge system. Use `artifact-system-designer`.
- The user needs pass/fail readiness review of an existing artifact set. Use `artifact-validation-workflow`.

## Related Skills

- Use `create-prd` first when product scope, user value, requirements, MVP boundaries, and release scope are still not defined.
- Use `artifact-system-designer` when the documentation pack is part of a larger controlled artifact system.
- Use `artifact-validation-workflow` after drafting when the user wants a readiness verdict, defect log, or acceptance checklist.
- Use `security-threat-model` or `security-best-practices` for deep security review before filling security docs.
- Use `improve-codebase-architecture` if the documentation exposes architecture problems that need refactoring, not just recording.

## Minimum Context Gate

Use available files, repo inspection, and conversation first. Ask only for missing details that materially change the documentation pack.

Required context:

1. Software scope: product, repo, module, platform, or service being documented.
2. Audience: solo developer, new engineer, AI coding agent, support engineer, ops owner, security reviewer, or leadership.
3. Current state: no docs, scattered docs, outdated docs, existing PRD, existing codebase, or pre-build planning.
4. Stack and deployment context if known.
5. Required output: folder structure only, file-by-file templates, filled starter docs, governance rules, checklist, or all of these.

If enough context exists, produce Version 1 with assumptions instead of blocking on a long questionnaire.

## Workflow

1. Classify the task:
   - Pre-build documentation scaffold.
   - Existing repo documentation upgrade.
   - Handoff or onboarding pack.
   - Production readiness documentation.
   - Documentation governance and maintenance system.
2. Identify source-of-truth files:
   - API truth: OpenAPI, route definitions, or generated API docs.
   - Data truth: migrations, schema snapshots, ERD, and data rules.
   - Infrastructure truth: IaC, deployment config, environment config, and secrets policy.
   - Product truth: PRD, requirements, user journeys, and release notes.
   - Decision truth: ADRs.
3. Design or tailor the docs structure. Do not create unused folders by default.
4. Draft the requested files or templates.
5. Add governance:
   - Definition of Done for PRs.
   - CODEOWNERS or owner mapping.
   - Review cadence.
   - CI checks when practical.
   - Drift metrics.
6. Add completeness gates for build, understand, operate, secure, and ship safely.
7. End with the smallest next action, usually the first 3 files to fill.

## Documentation Layers

Use these layers unless the repo needs a smaller subset:

1. Run: README, setup, commands, env vars, local seed data.
2. Product: overview, users, journeys, requirements, non-functional requirements, metrics.
3. Architecture: system context, containers, components, data flow, sequence diagrams, tradeoffs, ADRs.
4. Data: domain model, schema, ERD, migrations, retention, backups, quality.
5. API and integrations: auth, OpenAPI, errors, versioning, examples, third-party dependencies.
6. Frontend and backend: routes, state, components, services, jobs, configs, flags, caching.
7. Security: threat model, IAM/RBAC, secrets, encryption, audit logging, privacy, compliance mapping.
8. Reliability and operations: SLOs, SLIs, monitoring, logging, tracing, alerts, runbooks, incidents, postmortems.
9. Delivery and testing: environments, CI, CD, deployments, rollbacks, database changes, testing strategy.
10. Performance and edge cases: targets, load testing, scalability, error handling, offline sync, edge-case catalog.

## Output Rules

- Prefer a lean pack tailored to the project over copying every possible folder.
- Mark empty sections as `To fill` only when the user asked for templates. For final docs, replace placeholders with real content or list missing inputs.
- Keep PRDs and technical docs separate. Link them, but do not blur product requirements with operational runbooks.
- Make ownership explicit. Docs without owners rot.
- Make source-of-truth status explicit. Decks, summaries, and diagrams are not truth unless the user decides they are.
- Use the file-by-file template in `references/repo-docs-template.md` when a full pack is requested.

## Required Output Sequence

1. Direct recommendation: what documentation pack should be created or changed.
2. Assumptions and missing context.
3. Tailored folder structure.
4. Source-of-truth model.
5. File-by-file templates or starter content.
6. Governance rules.
7. Completeness checklist.
8. Next actions ranked by impact.
