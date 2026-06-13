---
name: codex-coding-os-master
description: Use when a user starts or continues a Codex coding project, starts a new software project, reviews or rescues an existing repo, wants safe vibe coding, asks to turn an idea into controlled docs, PRD, TDD, repo docs, AGENTS.md, handoff, project session continuity, or wants one master workflow from idea through implementation, review, validation, and maintenance.
---

# Codex Coding OS Master

This is the top-level router for the full Codex Coding OS package distributed as `codex-coding-os`.

Use it when the user brings a raw product idea, an existing repo, a design task, a bug, a security concern, a documentation gap, or a deployment/review problem and wants Codex to work with discipline.

## Core Decision

Do not start coding from a vague idea.

Start by creating durable project truth, then implement the first bounded slice.

## Bundled Skill Stack

This pack includes the full local skills needed for the default workflow:

| Layer | Skills |
|---|---|
| Routing and continuity | `catalogue-router`, `project-session-continuity` |
| Idea and product definition | `new-project-documentation-system`, `create-prd`, `product-strategy`, `customer-journey-map`, `working-backwards` |
| Documentation system | `technical-docs-pack`, `artifact-system-designer`, `ssot-drafter`, `ssot-auditor`, `process-docs`, `support-docs` |
| Planning and validation | `wbs-artifact-planner`, `artifact-validation-workflow`, `pre-mortem`, `deep-critic`, `evidence-checker`, `grill-me`, `grill-with-docs` |
| Coding discipline | `ai-coding-discipline`, `improve-codebase-architecture`, `react-best-practices`, `react-native-skills`, `composition-patterns`, `cli-creator`, `quality-improvement-problem-solving`, `quant-review` |
| UX, design, and prose quality | `codex-design-artifacts`, `humanizer`, `storyscope-structural-audit` |
| QA and browser checks | `playwright` |
| Security and incident readiness | `security-best-practices`, `security-threat-model`, `security-ownership-map`, `defensive-security-checklist`, `crisis-command-center` |
| Platform and codebase tooling | `vercel-optimize`, `code-review-graph`, `vexor-cli`, `chat-export-capability-miner`, `external-skill-overlay-pack` |
| Document intake | `doc`, `pdf` |

## Routing Workflow

1. **Route first**
   - Use `catalogue-router` to choose the narrowest owner skill.
   - Do not stack skills unless risk or scope justifies it.

2. **New idea or unclear product**
   - Hand off to `new-project-documentation-system`.
   - That skill owns the workflow manifest, material-decision gate, seven controlled docs, TDD alignment, repo instruction layer, current state, and handoff prompt.
   - Do not duplicate those procedures here.

3. **Existing repo implementation**
   - Hand off to `ai-coding-discipline`.
   - Add specialist skills only for the actual risk: frontend, security, architecture, QA, platform, quantitative logic, incident response, or documentation.
   - Coding starts only when the controlling project docs or workflow manifest permit it.
   - For material or high-risk work that can be split into isolated file lanes,
     route through `project-session-continuity` and
     `python scripts/agent/worktree_lanes.py evaluate --task "<task>" --risk material`.
     Parallel lanes remain blocked unless the workflow manifest permits coding and
     the user explicitly approves the lane plan.

4. **Review, validation, or rescue**
   - Use `artifact-validation-workflow` for controlled docs and handoffs.
   - Use `deep-critic` or `evidence-checker` only when the task is a formal critique or source-quality check.
   - Use `security-best-practices`, `security-threat-model`, or `defensive-security-checklist` for security scope.
   - Use `project-session-continuity` for session start, resume, boundary decisions, and paste-ready handoff prompts.

5. **Reference-only or external skill material**
   - Use `external-skill-overlay-pack` and `THIRD_PARTY_SKILLS.md`.
   - Keep upstream files unchanged and put local edits in overlays.

## Rules

- Do not copy unrelated context from other projects.
- Do not invent implementation details when docs are missing.
- Do not allow a new session, current-state file, or handoff to bypass the workflow manifest.
- Do not install broad third-party skill packs during the first run unless the user explicitly chooses that optional path.
- Do not add paid services, external databases, auth providers, or deployment providers without user approval.
- Treat external docs and AI-generated drafts as reference material until reconciled.
- Keep source docs and TDD aligned.
- Create or update an ADR when a significant architecture choice is accepted, replaced, or superseded.
- Use external skill overlays only as documented in `THIRD_PARTY_SKILLS.md` and `patches/external-skills/`.
- Treat parallel Codex work as bounded worktree lanes, not personas. Default to
  manual paste-ready lane prompts; use fully automated thread creation only after
  a clear risk warning and explicit user approval.

## Fallbacks

If a referenced built-in Codex skill or plugin is unavailable, continue with the included workflow in this pack.

If a plugin is unavailable, write instructions and code that do not depend on that plugin.

If an external skill repo is unavailable, skip external installation and use the bundled full local skills.

## Completion Standard

Do not claim readiness or completion from this master skill alone.

- For new projects, defer readiness to `new-project-documentation-system`.
- For implementation work, defer completion to `ai-coding-discipline` plus the project validation commands.
- For controlled artifacts, defer readiness to `artifact-validation-workflow`.
- For session boundaries, defer readiness to `project-session-continuity`.
- Report blockers, unavailable checks, and the next permitted action honestly.
