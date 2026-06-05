# Coding OS Capability Catalogue

This catalogue is the portable routing source bundled with `codex-coding-os-starter`.

## Fast Router

| Task | Primary skill | Supporting skills |
|---|---|---|
| Raw idea to PRD | `new-project-documentation-system` | `create-prd`, `product-strategy`, `customer-journey-map`, `working-backwards` |
| New project setup | `new-project-documentation-system` | `technical-docs-pack`, `wbs-artifact-planner`, `artifact-validation-workflow` |
| Full repo docs | `technical-docs-pack` | `artifact-system-designer`, `ssot-drafter`, `artifact-validation-workflow` |
| AI-assisted coding discipline | `ai-coding-discipline` | `improve-codebase-architecture`, `pre-mortem`, `deep-critic` |
| Capability mining from prior chats | `chat-export-capability-miner` | `catalogue-router`, `external-skill-overlay-pack` |
| Design artifact or prototype | `codex-design-artifacts` | `humanizer`, `storyscope-structural-audit`, `playwright` |
| Hard planning interview | `grill-me` | `pre-mortem`, `deep-critic` |
| Hard planning interview grounded in repo docs | `grill-with-docs` | `improve-codebase-architecture`, `technical-docs-pack` |
| Architecture improvement | `improve-codebase-architecture` | `code-review-graph`, `vexor-cli`, `technical-docs-pack` |
| React web work | `react-best-practices` | `composition-patterns`, `playwright`, `security-best-practices` |
| React Native work | `react-native-skills` | `react-best-practices`, `playwright` |
| Frontend QA | `playwright` | `react-best-practices`, `technical-docs-pack` |
| Security defaults | `security-best-practices` | `security-threat-model`, `security-ownership-map`, `defensive-security-checklist` |
| Defensive security checklist | `defensive-security-checklist` | `security-best-practices`, `security-threat-model`, `codex-security:*` |
| AppSec threat model | `security-threat-model` | `security-best-practices`, `artifact-validation-workflow` |
| Security ownership from git history | `security-ownership-map` | `code-review-graph` |
| Incident or outage response | `crisis-command-center` | `process-docs`, `security-best-practices`, `quality-improvement-problem-solving` |
| Recurring defects and RCA | `quality-improvement-problem-solving` | `evidence-checker`, `pre-mortem`, `artifact-validation-workflow` |
| Numeric logic, capacity, cost, or KPI checks | `quant-review` | `evidence-checker`, `deep-critic` |
| Prose and public README polish | `humanizer` | `storyscope-structural-audit`, `evidence-checker` |
| PRD, journey, or memo structure polish | `storyscope-structural-audit` | `humanizer`, `create-prd`, `working-backwards` |
| Vercel performance and cost optimization | `vercel-optimize` | `react-best-practices`, `playwright` |
| CLI creation | `cli-creator` | `ai-coding-discipline`, `technical-docs-pack` |
| Source-backed critique | `evidence-checker` | `deep-critic`, `artifact-validation-workflow` |
| Delivery sequencing | `wbs-artifact-planner` | `pre-mortem`, `artifact-validation-workflow` |
| Process or runbook docs | `process-docs` | `support-docs`, `ssot-drafter` |
| DOCX or PDF source intake | `doc`, `pdf` | `new-project-documentation-system`, `technical-docs-pack` |

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
