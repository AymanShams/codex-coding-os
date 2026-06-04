---
name: codex-coding-os-master
description: Use when a user starts a first Codex coding project, wants to vibe code safely, asks to turn an idea into a PRD, wants a complete project kickoff workflow, needs repo AGENTS files, or wants one master workflow from idea to first implementation slice.
---

# Codex Coding OS Master

This is the main router for the pack.

Use it when the user brings a raw product idea and wants Codex to help build it safely.

## Core Decision

Do not start coding from a vague idea.

Start by creating durable project truth, then implement the first bounded slice.

## Default Workflow

1. **Classify the project**
   - web app
   - mobile app
   - API or backend service
   - automation or internal tool
   - data tool
   - mixed product

2. **Run first intake**
   - Use `first-project-prd-workflow`.
   - Ask only questions that affect product scope, users, data, security, integrations, deployment, costs, or first release.
   - If enough detail exists, state assumptions and proceed.

3. **Create controlled source docs**
   - PRD
   - app-flow-doc
   - tech-stack-doc
   - frontend-guidelines
   - backend-structure
   - security-guidelines
   - implementation-plan

4. **Create the TDD build plan**
   - Use a plain step-by-step technical design document.
   - Include file and folder list, pages, buttons, data model, API routes, tests, env placeholders, and implementation sequence.

5. **Create repo instruction layer**
   - Use `repo-agent-instructions-pack`.
   - Add root `AGENTS.md`.
   - Add scoped `AGENTS.md` files for major folders when needed.
   - Add a handoff note and paste-ready next-chat prompt.

6. **Start implementation**
   - Use `ai-coding-discipline-pack`.
   - Pick one vertical slice.
   - Read before writing.
   - Make the smallest correct change.
   - Verify before completion.

7. **Review and validate**
   - Use `simplify-review-gate`.
   - Use `security-prelaunch-gate` when auth, data, payments, exports, admin, or public endpoints exist.
   - Use `frontend-qa-gate` for UI work.

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

- Do not copy private context from other projects.
- Do not invent implementation details when docs are missing.
- Do not install broad third-party skill packs during the first run.
- Do not add paid services, external databases, auth providers, or deployment providers without user approval.
- Treat external docs and AI-generated drafts as reference material until reconciled.
- Keep source docs and TDD aligned.

## Fallbacks

If a referenced built-in Codex skill is unavailable, continue with the included workflow in this pack.

If a plugin is unavailable, write instructions and code that do not depend on that plugin.

## Completion Standard

Do not claim the project is ready to code until these exist or are explicitly deferred:

- controlled PRD or project brief
- first implementation plan
- security baseline
- repo AGENTS instructions
- validation commands or validation placeholders
- known blockers

