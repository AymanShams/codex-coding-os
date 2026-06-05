# Component classification

## Bundled full local skill components

| Component group | Included as | Reason |
|---|---|---|
| Master coding workflow | `codex-coding-os-master` | One entrypoint for first-project Codex work |
| Portable routing | `catalogue-router` plus bundled catalogue | Routes work without depending on a machine-specific catalogue |
| Project kickoff and source-locked docs | `new-project-documentation-system` | Full orchestration from source intake to controlled docs, TDD, repo instructions, and validation |
| PRD and product framing | `create-prd`, `product-strategy`, `customer-journey-map`, `working-backwards` | Turns raw idea into usable product truth |
| Technical repo documentation | `technical-docs-pack` plus `repo-docs-template.md` | Full repo docs structure, governance, ownership, and documentation rot controls |
| Artifact system and validation | `artifact-system-designer`, `artifact-validation-workflow`, `ssot-drafter`, `ssot-auditor`, `process-docs`, `support-docs` | Controlled artifacts, handoff, acceptance gates, and operational docs |
| Execution planning and critique | `wbs-artifact-planner`, `pre-mortem`, `deep-critic`, `evidence-checker` | Work breakdown, risk testing, source quality, and skeptical review |
| Coding discipline and architecture | `ai-coding-discipline`, `improve-codebase-architecture`, `react-best-practices`, `react-native-skills`, `composition-patterns`, `cli-creator` | Bounded AI coding, architecture review, frontend rules, and implementation discipline |
| QA and browser checks | `playwright` | CLI browser automation and visual verification support |
| Security | `security-best-practices`, `security-threat-model`, `security-ownership-map` | Secure coding, threat modeling, and ownership risk analysis |
| Platform and codebase tools | `vercel-optimize`, `code-review-graph`, `vexor-cli` | Performance, graph-backed review, and local CLI guidance |
| Document intake | `doc`, `pdf` | DOCX and PDF extraction or rendering when project sources arrive as documents |
| External overlay handling | `external-skill-overlay-pack` | Tracks third-party source treatment and local overlay edits |

## Bundled templates

| Template group | Files |
|---|---|
| First chat | `templates/first-codex-prompt.md` |
| Controlled docs | `project-brief.md`, `prd.md`, `app-flow-doc.md`, `tech-stack-doc.md`, `frontend-guidelines.md`, `backend-structure.md`, `security-guidelines.md`, `implementation-plan.md` |
| Technical build | `tdd.md`, `repo-docs-template.md` |
| Agent instructions | `repo-AGENTS.md`, `scoped-AGENTS.md`, `CLAUDE.md`, `handoff-note.md` |
| Validation | `review-checklist.md`, `validation-report.md` |

## Default Codex capabilities to use when available

| Capability | Treatment |
|---|---|
| Built-in Codex editing and shell tools | Use directly for repo work |
| Built-in or plugin-managed skills | Prefer named platform skills when available, but do not require them |
| AGENTS.md support | Use root and scoped templates |
| Browser or frontend verification tools | Use when available, otherwise fall back to bundled `playwright` guidance |

## Third-party components not bundled

| Component | Treatment |
|---|---|
| `forrestchang/andrej-karpathy-skills` | Optional install with overlay |
| `anthropics/skills` | Reference only |
| `MiniMax-AI/skills` | Reference only |
| `github/spec-kit` | Reference only |
| `agentsmd/agents.md` | Reference only |
| `affaan-m/ECC` | Reference only |
| `mukul975/Anthropic-Cybersecurity-Skills` | Reference only |

## Repository boundary

| Material | Treatment |
|---|---|
| Project code and generated documentation | Belong in the user's project repo |
| Deployment and connected-service configuration | Belong in the user's project repo |
| Prior conversation exports | Not included |
| Codex-managed plugin installation files | Managed through Codex |
| Unreviewed third-party source copies | Linked upstream instead of bundled |
