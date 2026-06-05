# Third-party skills and references

This file explains which skills are bundled in this pack and which external sources remain upstream references.

## Bundled skills

The bundled skills are full local skill folders, including references, assets, and scripts where available.

| Area | Bundled skills |
|---|---|
| Master routing | `codex-coding-os-master`, `catalogue-router` |
| Idea to project docs | `new-project-documentation-system`, `create-prd`, `product-strategy`, `customer-journey-map`, `working-backwards` |
| Docs and artifact systems | `technical-docs-pack`, `artifact-system-designer`, `artifact-validation-workflow`, `ssot-drafter`, `ssot-auditor`, `process-docs`, `support-docs` |
| Planning and critique | `wbs-artifact-planner`, `pre-mortem`, `deep-critic`, `evidence-checker` |
| Coding discipline and architecture | `ai-coding-discipline`, `improve-codebase-architecture`, `react-best-practices`, `react-native-skills`, `composition-patterns`, `cli-creator` |
| QA and browser work | `playwright` |
| Security | `security-best-practices`, `security-threat-model`, `security-ownership-map` |
| Platform and repo tooling | `vercel-optimize`, `code-review-graph`, `vexor-cli` |
| Local document intake | `doc`, `pdf` |

## External source manifest

Machine-readable source data lives in:

`external-skills/manifest.json`

Human install details live in:

`docs/external-skills-installation.md`

## External repositories

| Source | Link | Treatment | Install instruction |
|---|---|---|---|
| Andrej Karpathy Skills | https://github.com/forrestchang/andrej-karpathy-skills | Optional install with overlay | `.\scripts\install-external-skills.ps1 -Install forrestchang-andrej-karpathy-skills -ApplyOverlays` |
| Anthropic Skills | https://github.com/anthropics/skills | Reference only | Do not bulk-install. Select and review specific skills first. |
| MiniMax Skills | https://github.com/MiniMax-AI/skills | Reference only | Do not bulk-install. Use as packaging reference. |
| GitHub Spec Kit | https://github.com/github/spec-kit | Reference only | Use as a spec-driven development reference. This pack already has PRD, TDD, and source-locked docs flow. |
| AGENTS.md | https://github.com/agentsmd/agents.md | Reference only | Use for instruction-file conventions. This pack includes its own templates. |
| ECC | https://github.com/affaan-m/ECC | Reference only | Mine narrow harness-security and no-secrets rules. Do not install full ECC by default. |
| Anthropic Cybersecurity Skills | https://github.com/mukul975/Anthropic-Cybersecurity-Skills | Reference only | Use only for authorized defensive checklist mining. Do not install globally. |

## Overlay policy

If an external skill is installed later, keep upstream files unchanged.

Apply local edits as overlay files from:

`patches/external-skills/`

Current overlay:

| External source | Overlay |
|---|---|
| `forrestchang/andrej-karpathy-skills` | `patches/external-skills/forrestchang-andrej-karpathy-skills/CODING_OS_OVERLAY.md` |

## Local adaptation

The Karpathy-inspired rules are already incorporated into `ai-coding-discipline` as generalized coding rules:

- think before coding
- simplicity first
- surgical changes
- goal-driven execution
- read before write
- deterministic logic in code
- narrow verification before completion
- no broad autonomous agents on sensitive or production data

The overlay explains how to use this pack's project-doc, validation, and security gates beside the upstream external skill.

## Default Codex capabilities

Use these if the user's Codex environment provides them:

| Capability | How this pack uses it |
|---|---|
| `skill-creator` | Future skill creation or pack improvement |
| `skill-installer` | External skill installation when explicitly selected |
| AGENTS.md support | Persistent repo instructions and scoped rules |
| Shell and patch tools | Controlled local edits and validation |

## Optional plugin capabilities

These are useful when available, but the pack must still work without them:

| Capability | Use |
|---|---|
| Build Web Apps or Vercel skills | Next.js, React, deployment, UI, and browser verification |
| GitHub skills | PRs, issues, CI, and code review |
| Supabase skills | Database projects, migrations, SQL, and row-level security review |
| Codex Security skills | Security scans, threat modeling, finding validation |
| Browser or Playwright tools | Local frontend QA and screenshots |

## Repository boundary

This pack contains reusable workflow assets. Project code, generated documentation, deployment settings, and connected-service configuration belong in the user's project repo.
