# Coding OS Capability Catalogue

This catalogue is the portable routing source bundled with `codex-coding-os`.

## Fast Router

| Task | Mode | Primary skill | Supporting skills | Do not combine by default | Escalate when |
|---|---|---|---|---|---|
| Raw idea to PRD | Discovery / Drafting | `new-project-documentation-system` | `create-prd`, `working-backwards` | `deep-critic`, `pre-mortem` | User asks for hard critique, launch risk, or source-backed validation |
| New project setup | Discovery / Planning | `new-project-documentation-system` | `technical-docs-pack`, `wbs-artifact-planner` | `security-threat-model` | Repo, stack, auth, database, or deployment scope is known |
| Project session start, resume, or handoff | Coordination | `project-session-continuity` | `new-project-documentation-system` when the documentation manifest exists | implementation skills | Current state conflicts with controlling sources or the manifest blocks coding |
| Full repo docs | Documentation | `technical-docs-pack` | `artifact-system-designer`, `ssot-drafter` | `deep-critic`, `pre-mortem` | Docs must become release-gated or source-locked |
| AI-assisted coding discipline | Execution | `ai-coding-discipline` | `improve-codebase-architecture` | `deep-critic`, `pre-mortem` | Task is high-risk, shared, production-facing, or architecturally unclear |
| Capability mining from prior chats | Analysis | `chat-export-capability-miner` | `catalogue-router`, `external-skill-overlay-pack` | `product-strategy` | Extracted capabilities affect install, publishing, or security policy |
| Design artifact or prototype | Design / Build | `codex-design-artifacts` | `humanizer`, `playwright` | `storyscope-structural-audit` | The artifact is public-facing or needs structure critique |
| Hard planning interview | Review / Discovery | `grill-me` | `pre-mortem` | `deep-critic`, `artifact-validation-workflow` | User asks for formal critique or validation |
| Hard planning interview grounded in repo docs | Review / Discovery | `grill-with-docs` | `technical-docs-pack` | `deep-critic` | Repo docs conflict or architecture claims need validation |
| Architecture improvement | Architecture Review | `improve-codebase-architecture` | `code-review-graph`, `technical-docs-pack` | `pre-mortem` | Change crosses modules, contracts, auth, data, or deployment |
| React web work | Implementation | `react-best-practices` | `composition-patterns`, `playwright` | `security-threat-model` | Auth, payments, sensitive data, or database writes are involved |
| React Native work | Implementation | `react-native-skills` | `react-best-practices`, `playwright` | `composition-patterns` | Shared component architecture or web parity is required |
| Frontend QA | Verification | `playwright` | `react-best-practices` | `humanizer` | Visual polish, accessibility, or design acceptance is failing |
| Security defaults | Security Review | `security-best-practices` | `defensive-security-checklist` | `security-threat-model` | Assets, trust boundaries, or data flows are known |
| Defensive security checklist | Security Review | `defensive-security-checklist` | `security-best-practices`, `codex-security:*` | `security-threat-model` | Threat model scope is concrete enough to map abuse cases |
| AppSec threat model | Security Review | `security-threat-model` | `security-best-practices`, `artifact-validation-workflow` | `deep-critic` | Findings need remediation planning or release gating |
| Security ownership from git history | Security Analysis | `security-ownership-map` | `code-review-graph` | `security-threat-model` | Ownership hotspots map to real assets or incidents |
| Incident or outage response | Incident | `crisis-command-center` | `process-docs`, `security-best-practices` | `humanizer`, `storyscope-structural-audit` | Incident becomes RCA, public comms, or prevention work |
| Recurring defects and RCA | RCA | `quality-improvement-problem-solving` | `evidence-checker`, `artifact-validation-workflow` | `deep-critic` | Evidence is weak or corrective actions affect release gates |
| Numeric logic, capacity, cost, or KPI checks | Quant Review | `quant-review` | `evidence-checker` | `deep-critic` | Assumptions are strategic, financial, or operationally material |
| Prose and public README polish | Writing | `humanizer` | `evidence-checker` | `storyscope-structural-audit` | Structure, positioning, or credibility is the main issue |
| PRD, journey, or memo structure polish | Structural Review | `storyscope-structural-audit` | `humanizer`, `create-prd` | `deep-critic` | The user asks for skeptical validation or source checking |
| Vercel performance and cost optimization | Platform Review | `vercel-optimize` | `react-best-practices`, `playwright` | `security-threat-model` | Auth, cache correctness, deployment safety, or cost spikes are involved |
| CLI creation | Implementation | `cli-creator` | `ai-coding-discipline`, `technical-docs-pack` | `codex-design-artifacts` | CLI mutates files, calls services, or handles credentials |
| Source-backed critique | Review / Validation | `evidence-checker` | `deep-critic` | `grill-me`, `pre-mortem` | Source quality is weak, claims conflict, or recency matters |
| Delivery sequencing | Planning | `wbs-artifact-planner` | `pre-mortem`, `artifact-validation-workflow` | `deep-critic` | Dependencies, risks, or acceptance gates are unclear |
| Process or runbook docs | Documentation | `process-docs` | `support-docs`, `ssot-drafter` | `humanizer` | It becomes a controlled SOP or audit artifact |
| DOCX or PDF source intake | Source Intake | `doc`, `pdf` | `new-project-documentation-system`, `technical-docs-pack` | `humanizer` | Extracted source becomes canonical project documentation |

## Conflict Control

- Use one primary skill by default.
- Add supporting skills only when they materially change the answer or workflow.
- Active installed capabilities are the only automatic primary owners.
- Candidate, project-local, and reference-only entries may be consulted only
  after active options have been checked, only when they materially improve the
  current session, and only after explicit user authorization.
- Candidate, project-local, and reference-only entries are session-only support.
  They must never become primary skills or universal installs by default.
- Do not stack critique, pre-mortem, evidence, validation, and grill skills unless the user asks for a formal review or the work is high-risk.
- If two skills overlap, choose the skill whose description most directly matches the requested output.
- Prefer execution skills for implementation tasks and critique skills for explicit review tasks.
- Prefer evidence-checking when source quality, recency, or factual accuracy is the core risk.

## Bundled Full Local Skills

- `ai-coding-discipline`
- `artifact-system-designer`
- `artifact-validation-workflow`
- `catalogue-router`
- `chat-export-capability-miner`
- `cli-creator`
- `code-review-graph`
- `codex-design-artifacts`
- `composition-patterns`
- `create-prd`
- `crisis-command-center`
- `customer-journey-map`
- `deep-critic`
- `defensive-security-checklist`
- `doc`
- `evidence-checker`
- `grill-me`
- `grill-with-docs`
- `humanizer`
- `improve-codebase-architecture`
- `new-project-documentation-system`
- `project-session-continuity`
- `pdf`
- `playwright`
- `pre-mortem`
- `process-docs`
- `product-strategy`
- `quality-improvement-problem-solving`
- `quant-review`
- `react-best-practices`
- `react-native-skills`
- `security-best-practices`
- `security-ownership-map`
- `security-threat-model`
- `ssot-auditor`
- `ssot-drafter`
- `storyscope-structural-audit`
- `support-docs`
- `technical-docs-pack`
- `vercel-optimize`
- `vexor-cli`
- `wbs-artifact-planner`
- `working-backwards`

## External Reference Repositories

These are reference or optional install sources, not bundled source code unless separately reviewed.

| Source | Treatment |
|---|---|
| `forrestchang/andrej-karpathy-skills` | Reference. Local rules are already incorporated into `ai-coding-discipline`. Apply the overlay in `patches/external-skills/forrestchang-andrej-karpathy-skills/` only if that repo is installed separately. |
| `anthropics/skills` | Reference. Use for skill structure and examples. Do not bulk-copy into this pack without license and relevance review. |
| `MiniMax-AI/skills` | Reference. Useful for skill packaging patterns. Do not bulk-install by default. |
| `github/spec-kit` | Reference for spec-driven development. The pack implements its own spec-before-code path through PRD, TDD, and repo instructions. |
| `agentsmd/agents.md` | Reference for `AGENTS.md` conventions. The pack includes its own repo instruction templates. |
| `affaan-m/ECC` | Reference only. Mine narrow rules for harness security and no-secrets scanning. Do not install the full system by default. |
| `mukul975/Anthropic-Cybersecurity-Skills` | Reference only for authorized defensive security checklists. Do not install globally because it is broad and includes dual-use workflows. |
| `addyosmani/agent-skills` | Reference only. Mine checklist ideas selectively. Do not install as a second active coding methodology. |
| `rohunj/claude-build-workflow` | Reference only. Mine workflow patterns only. |
| `davila7/claude-code-templates` | Reference only. Mine templates only when a specific project needs them. |
| `supabase/agent-skills` | Reference only. Prefer Codex Supabase plugin and MCP when available. |
| `openai/openai-agents-python` | App architecture reference for Python agent applications. Do not install as a Codex skill. |

## Candidate Backlog

| Candidate | Status | Reason |
|---|---|---|
| Full external broad skill packs | Reference only | Tool sprawl and provenance risk outweigh default Coding OS value. |
| Security mega-packs | Reference only | Use bundled security skills first, then selectively mine defensive checklists. |
| Spec Kit CLI | Optional later | Useful for teams standardizing spec-driven development, but not required for first personal project flow. |
