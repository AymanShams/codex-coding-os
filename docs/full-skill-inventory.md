# Full skill inventory

This pack bundles full local skill directories for the Codex Coding OS workflow.

## Bundled skills

| Skill | Treatment | Notes |
|---|---|---|
| `codex-coding-os-master` | Pack master | Top-level workflow router for Codex software work |
| `catalogue-router` | Full portable local skill | Uses bundled `references/capability-catalogue.md` |
| `chat-export-capability-miner` | Full portable local skill | Mines prior AI chat exports for reusable skills, plugins, MCPs, tools, and workflow candidates |
| `ai-coding-discipline` | Full portable local skill | Karpathy-inspired coding discipline, generalized for sensitive data |
| `new-project-documentation-system` | Full portable local skill | Includes references and AGENTS/CLAUDE/handoff assets |
| `technical-docs-pack` | Full local skill | Includes exact `references/repo-docs-template.md` |
| `create-prd` | Full local skill | PRD creation |
| `product-strategy` | Full local skill | Product direction before PRD where needed |
| `customer-journey-map` | Full local skill | User flow and friction analysis |
| `working-backwards` | Full local skill | PR/FAQ and decision artifacts |
| `wbs-artifact-planner` | Full local skill | Work breakdown and delivery sequencing |
| `artifact-system-designer` | Full local skill | Controlled artifact and documentation systems |
| `artifact-validation-workflow` | Full local skill | Acceptance gates and readiness verdicts |
| `ssot-drafter` | Full local skill | Controlled source-of-truth drafting |
| `ssot-auditor` | Full local skill | Controlled artifact review |
| `process-docs` | Full local skill | Lightweight process and runbook docs |
| `support-docs` | Full local skill | Support and help documentation |
| `doc` | Full local skill | DOCX intake and creation |
| `pdf` | Full local skill | PDF intake |
| `evidence-checker` | Full portable local skill | Source-quality and factual verification |
| `deep-critic` | Full local skill | Skeptical critique |
| `grill-me` | Full local skill | One-question-at-a-time pressure testing of plans, architectures, and decisions |
| `grill-with-docs` | Full local skill | Pressure testing against codebase docs, ADRs, terminology, and domain model |
| `pre-mortem` | Full local skill | Failure-first planning |
| `improve-codebase-architecture` | Full local skill | Architecture and refactor review |
| `react-best-practices` | Full local skill | React implementation rules |
| `react-native-skills` | Full local skill | React Native implementation rules |
| `composition-patterns` | Full local skill | Component composition rules |
| `cli-creator` | Full local skill | CLI creation |
| `codex-design-artifacts` | Full local skill | Design-heavy HTML artifacts, UI prototypes, visual concepts, and verification |
| `humanizer` | Full local skill | Public README, PRD, memo, and docs prose polish |
| `storyscope-structural-audit` | Full local skill | Structural anti-default review for PRDs, journeys, memos, and feature ideas |
| `quality-improvement-problem-solving` | Full local skill | RCA, recurrence prevention, defect investigation, and controlled improvement |
| `quant-review` | Full local skill | Numeric logic, assumptions, units, formulas, sensitivity, and KPI checks |
| `playwright` | Full local skill | CLI browser automation, assets, and scripts |
| `security-best-practices` | Full local skill | Framework-specific security references |
| `security-threat-model` | Full local skill | AppSec threat models |
| `security-ownership-map` | Full local skill | Ownership and security hotspot graph scripts |
| `defensive-security-checklist` | Full local skill | Defensive checklists, hardening plans, supply-chain security, and agent/MCP safety |
| `crisis-command-center` | Full local skill | Incidents, outages, evidence logs, containment, recovery, and after-action reviews |
| `vercel-optimize` | Full local skill | Vercel performance and cost optimization references and scripts |
| `code-review-graph` | Full portable local skill | Graph-backed code review entrypoints |
| `vexor-cli` | Full local skill | CLI helper guidance |
| `external-skill-overlay-pack` | Pack master | External source handling and overlays |

## Removed from the earlier skeleton

These abbreviated skills were removed because full local skills now cover their responsibilities:

- `ai-coding-discipline-pack`
- `first-project-prd-workflow`
- `frontend-qa-gate`
- `repo-agent-instructions-pack`
- `security-prelaunch-gate`
- `simplify-review-gate`
- `source-locked-docs-workflow`

## Portability changes

- Machine-specific paths were replaced with portable paths.
- Domain-specific examples were generalized.
- Sensitive-data wording was kept generic.
- The catalogue router uses a bundled catalogue.
