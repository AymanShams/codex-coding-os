---
name: ai-coding-discipline
description: Use when starting, planning, reviewing, or rescuing AI-assisted coding work, especially "vibe coding", Codex/Claude/Gemini/Goose/Agor workflows, new app builds, long-running agent sessions, multi-agent coding, worktree-based execution, or avoiding context sprawl and tool over-installation.
metadata:
  short-description: Keep AI coding spec-driven, bounded, reviewed, and verified
---

# AI Coding Discipline

Use this skill to prevent AI coding from turning into unbounded prompt-and-pray work. The goal is not to slow down coding. The goal is to keep the model anchored to source context, scoped work, human review, and verification.

## Attribution Note

The four common "Karpathy rules" should be treated as Karpathy-inspired, not verified verbatim Karpathy text. The source pattern came through `forrestchang/andrej-karpathy-skills`, which presents the rules as a `CLAUDE.md` instruction layer derived from Karpathy's observations about wrong assumptions, overcomplication, unintended adjacent edits, and goal-driven looping.

Use the substance, but do not overstate the attribution.

## First Decision

Classify the task before choosing a workflow:

| Task Shape | Use |
|---|---|
| Tiny edit with obvious file and test | Normal repo workflow. Keep context small. |
| New feature, module, refactor, or unfamiliar codebase | Full discipline loop below. |
| Long-running or multi-agent work | Full loop plus branch/worktree isolation. |
| External agent such as Goose, Agor, Factory, Jules, or cloud agents | Full loop plus manual approval and no secrets. |

## Core Rules

These are the durable operating rules to apply before and during AI coding:

1. **Think Before Coding**
   - State assumptions only when they affect architecture, data integrity, security, cost, user-visible behavior, or verification.
   - Ask before guessing when ambiguity can create rework or risk.
   - Push back when a simpler path exists.

2. **Simplicity First**
   - Write the minimum code that solves the real problem.
   - Avoid speculative features, one-use abstractions, and new dependencies without approval.
   - Do not confuse simplicity with under-engineering. Security, validation, logging, and rollback are not speculative when production risk exists.

3. **Surgical Changes**
   - Touch only what the task requires.
   - Do not improve adjacent code, comments, formatting, or naming unless that is part of the requested task.
   - For deliberate refactors, state the boundary first. Surgical does not mean refusing a refactor when refactor is the task.

4. **Goal-Driven Execution**
   - Define success criteria before editing.
   - Verification must name concrete checks: tests, lint, typecheck, build, migration validation, browser check, or manual acceptance criteria.
   - Continue until the goal is verified or clearly state what remains unverified.

## Discipline Loop

1. **Context gate**
   - Read `AGENTS.md`, project rules, and relevant repo docs first.
   - Run `catalogue-router` for non-trivial work so existing skills, MCPs, and project-local tools are checked before adding new tools.
   - Identify the stack, package manager, test commands, build commands, database layer, environment files, and deployment target.

2. **Request**
   - Convert the user request into a short build request with goal, users, scope, non-goals, constraints, and acceptance criteria.
   - Cut nice-to-haves before implementation. Scope is a control mechanism.

3. **Spec**
   - For material work, write or validate a technical spec before planning.
   - Inputs should include the request, project rules, current codebase or starter template, and constraints.
   - Prune contradictions and extra architecture before moving forward.

4. **Plan**
   - Produce an implementation plan with ordered steps.
   - Each step must name concrete files or modules, expected changes, rationale, verification, and rollback notes if risk is non-trivial.
   - If the task spans multiple modules, split it into milestones with clean context for each milestone.

5. **Execute**
   - Work one bounded step at a time.
   - Use a branch or git worktree for risky work, parallel agents, or external agent execution.
   - Prefer existing project patterns and installed skills over new abstractions.
   - Update the plan only when code reality proves the plan wrong.

6. **Review**
   - Read the diff, not only the model summary.
   - Require "what changed and why" for non-trivial edits.
   - Reject unrequested scope expansion, speculative abstractions, silent dependency changes, and hidden behavior changes.

7. **Verify**
   - Run the narrowest meaningful tests first, then broader checks if the blast radius justifies them.
   - For frontend work, use browser verification when the app can run.
   - Do not claim completion until the acceptance criteria have been checked or the unverified parts are clearly stated.

## Extra Guardrails From The 12-Rule Expansion

- **Deterministic logic belongs in code.** Do not use a model for retries, routing, permissions, escalation thresholds, status handling, sorting, filtering, validation, billing calculations, or migration success criteria when code, enums, schemas, or configuration can decide.
- **Use phase limits and checkpoints.** For multi-step work, checkpoint after each meaningful step with files changed, verification done, current risk, and next step.
- **Surface conflicts.** When codebase patterns disagree, do not average them. Prefer the affected module convention, then tested convention, then clearly intentional newer convention, then ask.
- **Read before write.** Read the target file, relevant imports, direct callers or consumers, and relevant tests before editing.
- **Tests are not the whole goal.** Tests must prove behavior, include negative cases where relevant, and cover the regression or invariant being protected.
- **Convention beats novelty.** In existing code, match naming, file structure, error handling, logging, component style, test style, and API response shape.
- **Fail visibly.** Say "not verified", "partially complete", "tests not run", or "migration incomplete" when that is true. Silent partial success is failure.

## External Agent Safety

Use this policy when invoking Goose, Agor, Factory-like missions, Jules, or any agent that can run commands or edit files:

```text
Mode: Manual Approval or Smart Approval
Branch: New branch or isolated git worktree
Data: No sensitive regulated data, no production credentials, no live payment secrets
Scope: One bounded task per session
Rule: Agent must plan first and wait for approval before edits
Review: Human or Codex reads diff and rationale before merge
```

For Goose-like tools, add a `.gooseignore` or equivalent ignore policy, but do not treat it as a complete security boundary.

## Anti-Patterns To Stop

- One giant prompt: "build the whole app."
- Coding before reading repo rules and current patterns.
- Planning without concrete files or verification steps.
- Letting an agent run in autonomous mode on real production code.
- Passing sensitive regulated data, secrets, credentials, payment keys, or production database access to agent workflows.
- Installing broad skill packs or coding-agent ecosystems before checking the local catalogue.
- Accepting extra frameworks, dependencies, auth changes, or database changes because the model added them.
- Long sessions where early mistakes become assumed truth.

## Practical Defaults

- New app or module: request -> spec -> plan -> implementation -> verification.
- Existing repo change: read rules -> locate files -> plan minimal diff -> edit -> test -> summarize.
- Large product build: establish core architecture manually first, then use agent milestones for repeated modules.
- Multi-agent coding: one agent per isolated task, then integrate through human-reviewed diffs.
- Sensitive or regulated work: keep governance and safety constraints active, especially sensitive regulated data, audit logs, authentication, authorization, and regulated safety claims.

