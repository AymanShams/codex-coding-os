# Third-party skills and references

This file explains provenance, attribution, external references, and optional upstream installs.

The canonical full bundled-skill inventory is `docs/full-skill-inventory.md`.

Machine-readable external source data is `external-skills/manifest.json`.

The required release validation inventory is `pack.manifest.json`.

## License relationship

The top-level repository license is MPL-2.0. That license applies to this
repository's covered files. It does not relicense third-party files that carry their
own notices, and it does not apply to external repositories that are only linked here.

Before redistributing this pack or a derivative package, preserve bundled license
and notice files and review the provenance metadata in `external-skills/manifest.json`.

## Bundled skill groups relevant to provenance

The bundled skills are full local skill folders, including references, assets, and scripts where available. This table summarizes bundled groups that matter for third-party provenance, attribution, or external-source comparison.

| Area | Bundled skills |
|---|---|
| Master routing and continuity | `codex-coding-os-master`, `catalogue-router`, `project-session-continuity` |
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
| Addy Osmani Agent Skills | https://github.com/addyosmani/agent-skills | Reference only | Mine checklist ideas selectively. The active coding discipline is already in bundled local skills and Superpowers references. |
| Claude Build Workflow | https://github.com/rohunj/claude-build-workflow | Reference only | Mine workflow patterns only. Do not install wholesale into Codex. |
| Claude Code Templates | https://github.com/davila7/claude-code-templates | Reference only | Mine templates selectively when a specific project needs them. |
| Supabase Agent Skills | https://github.com/supabase/agent-skills | Reference only | Use as supporting reference for Supabase projects. Prefer the Supabase plugin and MCP when available. |
| OpenAI Agents Python | https://github.com/openai/openai-agents-python | Reference only | Use as an app-architecture reference when building Python agent applications. |

## Attribution policy

For skills or source material not created in this pack, the intent is to use public/open-source sources and preserve attribution through links, bundled license files, source notes, or reference manifests.

If a source, attribution, or license note is missing, open an issue or pull request with:

1. The affected file or skill.
2. The source repository or documentation URL.
3. The proposed attribution or correction.
4. Any license details that should be reflected.

Do not copy additional third-party skill code into this repo until license and provenance are reviewed.

## Overlay policy

If an external skill is installed later, keep upstream files unchanged.

Apply local edits as overlay files from:

`patches/external-skills/`

Current overlay:

| External source | Overlay |
|---|---|
| `forrestchang/andrej-karpathy-skills` | `patches/external-skills/forrestchang-andrej-karpathy-skills/CODING_OS_OVERLAY.md` |

## External source version policy

Every external source in `external-skills/manifest.json` uses the same provenance fields: `license`, `reviewed_at`, `pinned_commit`, `sha256`, and `pin_status`. Installable sources also declare the integrity control that the installer actually enforces.

Reference-only sources are not installed by this pack. Installable external sources should be pinned before repeatable public release instructions are published. The installer applies this rule generically to any installable external source and verifies that a pinned Git checkout resolves to the declared commit.

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
| Understand Anything | Codebase knowledge graphs, codebase Q&A, diffs, onboarding, and flow explanations |

## Repository boundary

This pack contains reusable workflow assets. Project code, generated documentation, deployment settings, and connected-service configuration belong in the user's project repo.
