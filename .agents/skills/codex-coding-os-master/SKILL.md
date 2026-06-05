---
name: codex-coding-os-master
description: Use when a user starts or continues a Codex coding project, wants to vibe code safely, asks to turn an idea into a PRD, wants a complete coding operating workflow, needs repo AGENTS files, or wants one master workflow from idea through implementation, review, validation, and maintenance.
---

# Codex Coding OS Master

This is the top-level router for the full Codex Coding OS package distributed as `codex-coding-os-starter`.

Use it when the user brings a raw product idea, an existing repo, a design task, a bug, a security concern, a documentation gap, or a deployment/review problem and wants Codex to work with discipline.

## Core Decision

Do not start coding from a vague idea.

Start by creating durable project truth, then implement the first bounded slice.

## Bundled Skill Stack

This pack includes the full local skills needed for the default workflow:

| Layer | Skills |
|---|---|
| Routing | `catalogue-router` |
| Idea and product definition | `new-project-documentation-system`, `create-prd`, `product-strategy`, `customer-journey-map`, `working-backwards` |
| Documentation system | `technical-docs-pack`, `artifact-system-designer`, `ssot-drafter`, `ssot-auditor`, `process-docs`, `support-docs` |
| Planning and validation | `wbs-artifact-planner`, `artifact-validation-workflow`, `pre-mortem`, `deep-critic`, `evidence-checker`, `grill-me`, `grill-with-docs` |
| Coding discipline | `ai-coding-discipline`, `improve-codebase-architecture`, `react-best-practices`, `react-native-skills`, `composition-patterns`, `cli-creator`, `quality-improvement-problem-solving`, `quant-review` |
| UX, design, and prose quality | `codex-design-artifacts`, `humanizer`, `storyscope-structural-audit` |
| QA and browser checks | `playwright` |
| Security and incident readiness | `security-best-practices`, `security-threat-model`, `security-ownership-map`, `defensive-security-checklist`, `crisis-command-center` |
| Platform and codebase tooling | `vercel-optimize`, `code-review-graph`, `vexor-cli`, `chat-export-capability-miner`, `external-skill-overlay-pack` |
| Document intake | `doc`, `pdf` |

## Default Workflow

1. **Route and classify**
   - Use `catalogue-router`.
   - web app
   - mobile app
   - API or backend service
   - automation or internal tool
   - data tool
   - mixed product

2. **Run first intake**
   - Use `new-project-documentation-system`.
   - Use `create-prd` for the PRD layer.
   - Use `product-strategy` and `customer-journey-map` when the idea is vague or the target user is weak.
   - Use `working-backwards` when the user needs a serious PR/FAQ or decision memo before build.
   - Ask only questions that affect product scope, users, data, security, integrations, deployment, costs, or first release.
   - If enough detail exists, state assumptions and proceed.

3. **Create controlled source docs**
   - Use `new-project-documentation-system`.
   - Produce the seven-doc pack before repo docs:
   - PRD
   - app-flow-doc
   - tech-stack-doc
   - frontend-guidelines
   - backend-structure
   - security-guidelines
   - implementation-plan

4. **Create the TDD build plan**
   - Use `wbs-artifact-planner` for work breakdown and sequencing.
   - Use a plain step-by-step technical design document.
   - Include file and folder list, pages, buttons, data model, API routes, tests, env placeholders, and implementation sequence.
   - Use `artifact-validation-workflow` to validate that the plan matches the controlled docs.

5. **Create repo instruction layer**
   - Use `new-project-documentation-system` assets.
   - Use `technical-docs-pack` for the full repo documentation template.
   - Add root `AGENTS.md`.
   - Add scoped `AGENTS.md` files for major folders when needed.
   - Add a handoff note and paste-ready next-chat prompt.

6. **Start implementation**
   - Use `ai-coding-discipline`.
   - Pick one vertical slice.
   - Read before writing.
   - Make the smallest correct change.
   - Verify before completion.

7. **Review and validate**
   - Use `deep-critic` for reasoning and evidence quality.
   - Use `artifact-validation-workflow` for docs and handoff artifacts.
   - Use `security-best-practices` and `security-threat-model` when auth, private data, payments, exports, admin, or public endpoints exist.
   - Use `defensive-security-checklist` for security hardening checklists, supply-chain checks, MCP/hook/agent safety reviews, and control gap planning.
   - Use `playwright` for frontend UI checks when a local or remote app can run.
   - Use `improve-codebase-architecture` when the implementation exposes structural problems.
   - Use `quality-improvement-problem-solving` when defects or incidents recur.
   - Use `quant-review` when calculations, capacity, costs, or KPI logic affect implementation.
   - Use `humanizer` and `storyscope-structural-audit` for public docs, PRDs, README files, and product narratives that must not read like generic AI output.
   - Use `codex-design-artifacts` for design-heavy prototypes, HTML artifacts, and visual UI concepts.
   - Use `crisis-command-center` for outages, incidents, escalations, evidence logs, and after-action reviews.

## First-Chat Output Standard

For a new project, produce:

- short assumptions list
- questions only where needed
- project brief
- seven-doc plan
- suggested repo structure
- first implementation slice
- validation plan
- risks and decisions that still need the user

## Rules

- Do not copy unrelated context from other projects.
- Do not invent implementation details when docs are missing.
- Do not install broad third-party skill packs during the first run unless the user explicitly chooses that optional path.
- Do not add paid services, external databases, auth providers, or deployment providers without user approval.
- Treat external docs and AI-generated drafts as reference material until reconciled.
- Keep source docs and TDD aligned.
- Use external skill overlays only as documented in `THIRD_PARTY_SKILLS.md` and `patches/external-skills/`.

## Fallbacks

If a referenced built-in Codex skill or plugin is unavailable, continue with the included workflow in this pack.

If a plugin is unavailable, write instructions and code that do not depend on that plugin.

If an external skill repo is unavailable, skip external installation and use the bundled full local skills.

## Completion Standard

Do not claim the project is ready to code until these exist or are explicitly deferred:

- controlled PRD or project brief
- first implementation plan
- security baseline
- repo AGENTS instructions
- validation commands or validation placeholders
- known blockers
