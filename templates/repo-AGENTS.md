# Project AGENTS.md

## Purpose
This repository is built with Codex assistance. The agent must preserve project truth, keep changes small, and verify behavior before reporting completion.

## Source of Truth
Read these files before making product or architecture changes:

1. `docs/project-brief.md`
2. `docs/prd.md`
3. `docs/app-flow-doc.md`
4. `docs/tech-stack-doc.md`
5. `docs/frontend-guidelines.md`
6. `docs/backend-structure.md`
7. `docs/security-guidelines.md`
8. `docs/implementation-plan.md`
9. `docs/tdd.md`

If these files conflict, ask for clarification or state the conflict before editing.

## Working Rules
- Read relevant files before writing code.
- Make the smallest correct change.
- Do not rewrite unrelated code.
- Do not add dependencies without explaining why.
- Do not commit secrets.
- Do not invent product behavior that is absent from the docs.
- Update docs when code changes the intended behavior.
- Run available validation before final response.

## Implementation Flow
1. Restate the requested change in one sentence.
2. Identify affected files.
3. Inspect current implementation.
4. Make the smallest useful change.
5. Run validation.
6. Summarize files changed and verification result.

## Validation
Use the project commands defined in `docs/implementation-plan.md` or the package scripts. If validation cannot run, explain why and identify the missing prerequisite.

## Security
- Server-side authorization is required for protected data and actions.
- Frontend hiding is not authorization.
- Environment variables must use placeholders in committed files.
- User input must be validated on the server.
- Logs must not contain secrets or sensitive user content.

## Completion Standard
Do not say a task is done unless:

- Code compiles or the failure is clearly explained.
- Relevant tests or checks were run.
- User-facing behavior is described.
- Any remaining risk is stated.
