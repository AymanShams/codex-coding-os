# Third-Party Skills And References

This file separates bundled content from references.

## Bundled In This Repo

The skills under `.agents/skills/` are authored for this pack.

They are designed to work even when no external skill pack is installed.

## Default Or Built-In Codex Capabilities

Use these if the user's Codex environment provides them:

| Capability | How This Pack Uses It |
|---|---|
| `skill-creator` | Create or improve future skills |
| `skill-installer` | Install external skills if the user chooses later |
| AGENTS.md support | Persistent repo instructions and scoped rules |
| Shell and apply patch tools | Controlled local edits and validation |

## Optional Plugin Capabilities

These are useful if available, but this pack must still work without them:

| Capability | Use |
|---|---|
| Build Web Apps or Vercel skills | Next.js, React, deployment, UI, and browser verification |
| GitHub skills | PRs, issues, CI, and code review |
| Supabase skills | Database projects, migrations, SQL, and RLS review |
| Codex Security skills | Security scans, threat modeling, finding validation |
| Browser or Playwright tools | Local frontend QA and screenshots |

## Reference Only

Do not wholesale-install broad packs. Mine narrow ideas only.

| Source | Recommended Treatment |
|---|---|
| `agentsmd/agents.md` | Reference for root and nested `AGENTS.md` conventions |
| `forrestchang/andrej-karpathy-skills` | Reference for Karpathy-inspired coding rules already adapted here |
| `github/spec-kit` | Reference for spec-driven development, not a required dependency |
| `anthropics/skills` | Pattern library, not bundled |
| `MiniMax-AI/skills` | Pattern library, not bundled |
| `vercel-labs` examples | Reference for platform-specific app work |
| broad Claude Code template packs | Reference only, do not make them the foundation |

## Overlay Policy

If an external skill is installed later, do not edit it manually without tracking the change.

Use `scripts/apply-external-skill-overlays.ps1` to place a companion overlay file beside the external skill. That preserves the original upstream skill and makes local edits auditable.

## Excluded Material

The pack intentionally excludes:

- private company context
- client facts
- private project names
- account IDs
- API keys
- plugin cache files
- proprietary source documents

