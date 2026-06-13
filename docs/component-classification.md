# Component classification

## Bundled full local skill components

| Component group | Included as | Reason |
|---|---|---|
| Master coding workflow | `codex-coding-os-master` | One entrypoint for Codex software work |
| Portable routing | `catalogue-router` plus bundled catalogue | Routes work without depending on a machine-specific catalogue |
| Project kickoff and source-locked docs | `new-project-documentation-system` | Full orchestration from source intake to controlled docs, TDD, repo instructions, and validation |
| Project session continuity | `project-session-continuity` | Automated start, resume, live current-state, boundary decisions, and persistent handoffs |
| Parallel worktree lanes | `project-session-continuity` plus `scripts/agent/worktree_lanes.py` | Fail-closed lane offer, contract, worktree creation, validation, handoff, and cleanup controls |
| PRD and product framing | `create-prd`, `product-strategy`, `customer-journey-map`, `working-backwards` | Turns raw idea into usable product truth |
| Technical repo documentation | `technical-docs-pack` plus `repo-docs-template.md` | Full repo docs structure, governance, ownership, and documentation rot controls |
| Artifact system and validation | `artifact-system-designer`, `artifact-validation-workflow`, `ssot-drafter`, `ssot-auditor`, `process-docs`, `support-docs` | Controlled artifacts, handoff, acceptance gates, and operational docs |
| Execution planning and critique | `wbs-artifact-planner`, `pre-mortem`, `deep-critic`, `evidence-checker`, `grill-me`, `grill-with-docs` | Work breakdown, risk testing, source quality, skeptical review, and hard planning questions |
| Coding discipline and architecture | `ai-coding-discipline`, `improve-codebase-architecture`, `react-best-practices`, `react-native-skills`, `composition-patterns`, `cli-creator`, `quality-improvement-problem-solving`, `quant-review` | Bounded AI coding, architecture review, frontend rules, implementation discipline, RCA, and numeric logic review |
| Design and writing quality | `codex-design-artifacts`, `humanizer`, `storyscope-structural-audit` | Visual artifacts, UI concept work, README/prose polish, and non-generic PRD or memo structure |
| QA and browser checks | `playwright` | CLI browser automation and visual verification support |
| Security and incident readiness | `security-best-practices`, `security-threat-model`, `security-ownership-map`, `defensive-security-checklist`, `crisis-command-center` | Secure coding, threat modeling, ownership risk analysis, defensive checklists, and incident response |
| Platform and codebase tools | `vercel-optimize`, `code-review-graph`, `vexor-cli`, `chat-export-capability-miner` | Performance, graph-backed review, local CLI guidance, and capability mining |
| Document intake | `doc`, `pdf` | DOCX and PDF extraction or rendering when project sources arrive as documents |
| External overlay handling | `external-skill-overlay-pack` | Tracks third-party source treatment and local overlay edits |

## Bundled operational components

| Component group | Files | Reason |
|---|---|---|
| Pack source of truth | `pack.manifest.json`, `pack.schema.json` | Drives validation, release inventory, and manifest shape |
| Install and uninstall | `scripts/install.ps1`, `scripts/uninstall.ps1`, `scripts/install.sh`, `scripts/uninstall.sh` | Installs skills and support files, writes an install manifest, and removes from matching paths |
| Release safety | `scripts/validate-pack.ps1`, `scripts/release-safety-scan.ps1`, `scripts/package.ps1` | Validates required files, skill frontmatter, secret patterns, release exclusions, optional Git history scans, and generated ZIP contents |
| Parallel lane orchestration | `scripts/agent/worktree_lanes.py`, `hooks/worktree-lane-pre-commit.py`, `hooks/worktree-lane-pre-push.py` | Creates bounded worktrees only after approval and validates lane contracts before commit or push when hooks are enabled |
| CI and smoke test | `.github/workflows/validate.yml`, `tests/install-uninstall-smoke.ps1` | Runs validation, install/reinstall/uninstall test, and package build |
| Command approval templates | `.codex/rules/default.rules`, `docs/codex-rules.md` | Optional Codex command policy for destructive commands, installs, migrations, deployments, and secret exposure |
| MCP review | `docs/mcp-review-checklist.md` | Review gate for MCPs, connectors, and plugin-managed tools with sensitive permissions |

## Bundled templates

| Template group | Files |
|---|---|
| First chat | `templates/first-codex-prompt.md` |
| Controlled docs | `project-brief.md`, `prd.md`, `app-flow-doc.md`, `tech-stack-doc.md`, `frontend-guidelines.md`, `backend-structure.md`, `security-guidelines.md`, `implementation-plan.md` |
| Technical build | `tdd.md`, `repo-docs-template.md` |
| Agent instructions | `repo-AGENTS.md`, `scoped-AGENTS.md`, `CLAUDE.md`, `handoff-note.md` |
| Parallel lanes | `worktree-task-contract.md`, `parallel-worktree-offer.md`, `parallel-lane-handoff.md` |
| Validation | `review-checklist.md`, `fresh-context-review.md`, `validation-report.md` |

## Default Codex capabilities to use when available

| Capability | Treatment |
|---|---|
| Built-in Codex editing and shell tools | Use directly for repo work |
| Built-in or plugin-managed skills | Prefer named platform skills when available, but do not require them |
| AGENTS.md support | Use root and scoped templates |
| Browser or frontend verification tools | Use when available, otherwise fall back to bundled `playwright` guidance |
| Codex rules | Optional templates are bundled but not installed automatically |

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
| `addyosmani/agent-skills` | Reference only |
| `rohunj/claude-build-workflow` | Reference only |
| `davila7/claude-code-templates` | Reference only |
| `supabase/agent-skills` | Reference only |
| `openai/openai-agents-python` | Reference only |

## Repository boundary

| Material | Treatment |
|---|---|
| Project code and generated documentation | Belong in the user's project repo |
| Deployment and connected-service configuration | Belong in the user's project repo |
| Prior conversation exports | Not included |
| Codex-managed plugin installation files | Managed through Codex |
| Unreviewed third-party source copies | Linked upstream instead of bundled |
