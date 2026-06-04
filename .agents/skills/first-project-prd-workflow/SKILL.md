---
name: first-project-prd-workflow
description: Use when turning a raw project idea into a PRD, app flow, tech stack, frontend guidelines, backend structure, security guidelines, implementation plan, or first project brief before coding.
---

# First Project PRD Workflow

Use this skill to turn a rough idea into controlled product and engineering direction.

## Intake Rule

Ask questions when guessing would create serious rework or risk.

If the user is a beginner, explain questions in plain language and offer options.

## Minimum Question Areas

Cover these areas before writing final docs:

1. Product goal and problem.
2. Target users and roles.
3. Main workflows.
4. Must-have release scope.
5. Explicit non-goals.
6. Data entities and relationships.
7. Privacy and sensitive data.
8. Authentication and authorization.
9. Integrations.
10. Platform choice: web, mobile, backend, automation, or mixed.
11. Preferred stack if any.
12. Deployment and ownership.
13. Budget or paid service limits.
14. Success criteria.
15. First version acceptance criteria.

## Seven Controlled Docs

Create these in order.

### 1. PRD

Must include:

- product decision
- problem
- users
- jobs to be done
- scope
- non-goals
- main modules
- data and privacy assumptions
- success criteria
- acceptance criteria
- open questions

### 2. App Flow

Must include:

- user roles
- routes or screens
- main workflows
- empty states
- error states
- permissions by workflow
- notification or email triggers if any

### 3. Tech Stack

Must include:

- frontend choice
- backend choice
- database choice
- auth approach
- deployment approach
- local development assumptions
- portability constraints
- provider decisions that remain candidates

### 4. Frontend Guidelines

Must include:

- UI style
- layout patterns
- component rules
- accessibility baseline
- responsive behavior
- loading and error states
- design assets and icon policy

### 5. Backend Structure

Must include:

- modules
- services
- API routes
- validation approach
- authorization approach
- data access approach
- background jobs if any
- integration adapters

### 6. Security Guidelines

Must include:

- secrets policy
- auth and sessions
- authorization
- rate limits
- input validation
- logging and audit
- dependency checks
- data protection
- backup and rollback assumptions

### 7. Implementation Plan

Must include:

- milestones
- first vertical slice
- files or folders expected
- tests
- validation commands
- rollout plan
- risks
- stop conditions

## Drafting Standard

- Write as the project owner, not as an assistant explaining the document.
- Do not include meta language about the prompt, course, chat, or workflow.
- Keep placeholders only where a real decision is missing.
- Mark assumptions plainly.
- Never include private data.

## Completion

End with:

- created docs
- assumptions
- unresolved decisions
- recommended first implementation slice

