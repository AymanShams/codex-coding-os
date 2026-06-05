---
name: new-project-documentation-system
description: Use when the user asks Codex to start a new software project, turn a product idea or source folder into a complete project documentation system, create PRD plus app-flow, tech-stack, frontend, backend, security, implementation, TDD, repo docs, AGENTS.md, CLAUDE.md, or prepare a repo so Codex, Claude Code, or a developer can build later. This is an orchestration skill: route to existing skills for PRDs, SSOT documents, technical documentation packs, artifact systems, validation, security, and document extraction instead of duplicating their templates.
---

# New Project Documentation System

Use this skill to move from idea or source folder to a controlled, source-locked project documentation system and repo handoff. Keep this skill as the conductor. Do not copy the detailed templates from `technical-docs-pack`, `create-prd`, `ssot-drafter`, or `artifact-system-designer`.

## Operating Rule

Create durable project truth before scaffolding broad repo docs or writing application code.

If the user asks to start building immediately, first check whether the controlled source docs, TDD, repo instructions, and validation gates exist. If they do not exist, create or request approval to create them before coding.

## Capability Routing

Always start with `catalogue-router`, then delegate by phase:

| Phase | Use |
|---|---|
| Source extraction from DOCX/PDF/XLSX | `doc`, `document-skills:docx`, `document-skills:pdf`, `document-skills:xlsx` |
| Product requirements | `create-prd` |
| Formal controlled artifacts | `ssot-drafter` |
| Repo documentation tree and detailed templates | `technical-docs-pack` |
| Folder/source-of-truth operating model | `artifact-system-designer` |
| Delivery sequencing and implementation plan | `wbs-artifact-planner` |
| Security, privacy, and development controls | `security-best-practices` plus project-specific security skills when relevant |
| Readiness, drift, and pass/fail checks | `artifact-validation-workflow` |
| Skill updates | `skill-creator` |

Use `technical-docs-pack/references/repo-docs-template.md` for the detailed repo documentation template. Do not recreate it here.

## Workflow

1. **Route and scope.** Identify the company/project context, output path, source folders, expected formats, and whether code is allowed now.
2. **Inventory sources.** Build a manifest before drafting. Separate controlling sources, reference templates, external drafts, media, sensitive files, and restricted files. See `references/source-inventory-and-intake.md`.
3. **Ask material questions.** Ask only questions that change scope, architecture, data/privacy, security, integrations, roles, operations, repo setup, or deployment model. If enough context exists, draft Version 1 with explicit assumptions.
4. **Create controlled source docs.** Produce the seven-doc pack before repo docs: PRD, app flow, tech stack, frontend guidelines, backend structure, security guidelines, and implementation plan.
5. **Merge build-plan inputs.** If external TDD/build-plan drafts exist, preserve originals, compare against controlled sources, correct drift, and create one merged TDD. See `references/tdd-merge-and-source-lock.md`.
6. **Create repo documentation.** Use `technical-docs-pack` and its existing repo template. Apply the stage model in `references/documentation-stage-map.md` to decide what can be filled now and what must be marked not due.
7. **Add repo instruction layer.** Add root and scoped `AGENTS.md`, `CLAUDE.md`, and a handoff history note using `references/repo-agent-instructions.md` and the files in `assets/`.
8. **Validate before completion.** Run the gates in `references/validation-gates.md`. Report unavailable checks clearly.

## Non-Negotiable Gates

- Do not create broad repo docs before the seven controlled source docs and merged TDD exist, unless the user explicitly requests a blank template only.
- Do not use generic boilerplate text for filled docs. Filled docs must be source-locked.
- Do not mark implementation, operations, reliability, or maturity docs complete before implementation evidence exists.
- Do not push source exports, sensitive regulated data, private user files, secrets, local tool metadata, or generated credentials to Git.
- Do not let Supabase, Vercel, or any development-stage tool become a permanent architecture commitment unless the controlled docs say so.
- Do not duplicate detailed documentation templates from existing skills. Link to or invoke the owning skill.

## Outputs

For a full new-project run, produce or update:

- Controlled product documentation folder with Markdown first and DOCX when requested.
- Seven controlled source docs.
- Merged TDD/build plan.
- Documentation alignment review when external drafts or templates were reconciled.
- Repo documentation tree using `technical-docs-pack`.
- Root and scoped agent instruction files.
- Handoff history note and paste-ready new-chat prompt.
- Validation summary with missing checks and known blockers.

## Completion Standard

End with:

- created or updated paths
- skills used
- source folders/files used
- validation checks passed
- checks not run and why
- remaining decisions before coding

