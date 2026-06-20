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

## Outcome Control
The requested outcome controls the work. Source checks, skills, frameworks, state files, handoffs, review waits, pull request templates, GitHub checks, indexing, and coordination updates are support steps unless they are explicitly requested, clearly implied by the requested outcome, required by project rules, or needed to prevent a concrete safety, source-of-truth, security, compliance, or verification error.

This does not override mandatory project gates, validation checks, source-of-truth checks, security controls, compliance controls, or explicit user instructions.

Before turning a support step into a separate task, branch, pull request, artifact, or workflow, ask: does this directly complete the requested outcome, unblock it, satisfy a mandatory control, or prevent a concrete error? If not, record it as a note and return to the requested outcome.

If support work starts generating more support work, stop and report the loop. Continue only by returning to the requested outcome, completing the mandatory control, asking for approval, or explaining why the real task is blocked.

## Working Rules
- At the start of a new or resumed non-trivial session, run `python scripts/agent/session_continuity.py start --start-new` when available.
- Use `python scripts/agent/session_continuity.py start --continue-slice` only when continuing the same bounded active slice with known dirty work after inspection.
- Read `docs/delivery/current-state.md`, `docs/delivery/active-slice-manifest.json`, its latest handoff, and `project-documentation-manifest.json` before editing.
- Classify non-trivial work through five layers: container, action, domain, risk/validation, and authority. Use container plus action for the primary workflow owner. Use material domain and risk evidence for supporting routes. Let project authority, manifests, source docs, and explicit user limits override routing hints.
- For reviews, audits, validations, comparisons, and rescue work, re-check routing after inspecting the artifact, diff, file list, or source set. Add specialist support for material frontend, security, data, controlled-document, quantitative, evidence, creative, browser, or project-governance needs without changing the primary owner unless the initial classification was wrong.
- Do not treat bare framework-adjacent words as domain evidence. Route domain support only from exact framework identifiers, filenames, framework phrases, changed files, or ordinary words paired with material domain context.
- Read relevant files before writing code.
- Make the smallest correct change.
- Do not rewrite unrelated code.
- Do not add dependencies without explaining why.
- Do not commit secrets.
- Do not invent product behavior that is absent from the docs.
- Update docs when code changes the intended behavior.
- Run available validation before final response.
- Do not start implementation unless the workflow manifest and active-slice manifest both permit coding.
- Treat current state and handoffs as coordination records, not product or technical authority.
- Treat review markers and notifications as evidence to inspect, not permission to merge, deploy, or bypass validation.
- Keep this root file for repo-wide rules. Put folder-specific rules in scoped `AGENTS.md` files and detailed task procedures in skills or docs.

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

Before crossing into a different meaningful slice, run `python scripts/agent/session_continuity.py decide` when available and prepare the required persistent handoff.
