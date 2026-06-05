# External Skills Installation And Overlay Guide

## Direct Decision

Do not bulk-install external skill packs for a first project.

The bundled local skills are the primary system. External repositories are reference sources or optional installs when a user knowingly wants the upstream behavior too.

## Why

External skill packs create four risks:

- license and attribution drift
- duplicate or conflicting instructions
- broad tool behavior that was not designed for this pack
- supply-chain and maintenance risk

## External Sources

| Source | Link | Treatment |
|---|---|---|
| Andrej Karpathy Skills | https://github.com/forrestchang/andrej-karpathy-skills | Optional install with overlay |
| Anthropic Skills | https://github.com/anthropics/skills | Reference only |
| MiniMax Skills | https://github.com/MiniMax-AI/skills | Reference only |
| GitHub Spec Kit | https://github.com/github/spec-kit | Reference only |
| AGENTS.md | https://github.com/agentsmd/agents.md | Reference only |
| ECC | https://github.com/affaan-m/ECC | Reference only |
| Anthropic Cybersecurity Skills | https://github.com/mukul975/Anthropic-Cybersecurity-Skills | Reference only |

## One-Pack Optional Install

From the repo root:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install-external-skills.ps1 -List
.\scripts\install-external-skills.ps1 -Install forrestchang-andrej-karpathy-skills -ApplyOverlays
```

This clones the external source into `.external-sources/`, installs only the selected skill path when found, and applies this pack's overlay file.

## Manual Install Pattern

Use this only when the user asks to install an external source manually.

```powershell
git clone https://github.com/forrestchang/andrej-karpathy-skills .external-sources\forrestchang-andrej-karpathy-skills
New-Item -ItemType Directory -Force -Path "$HOME\.agents\skills" | Out-Null
Copy-Item .external-sources\forrestchang-andrej-karpathy-skills\skills\karpathy-guidelines "$HOME\.agents\skills\karpathy-guidelines" -Recurse -Force
.\scripts\apply-external-skill-overlays.ps1
```

## Overlay Rule

Never edit upstream external skill files without tracking the delta.

Put local changes in an overlay file under `patches/external-skills/<source-id>/`.

## Current Overlays

| Source | Overlay |
|---|---|
| `forrestchang-andrej-karpathy-skills` | `patches/external-skills/forrestchang-andrej-karpathy-skills/CODING_OS_OVERLAY.md` |

## Review Before Public Release

- Re-check each external link.
- Confirm license compatibility.
- Do not imply endorsement by the external authors.
- Do not claim Karpathy wording is verbatim.
- Keep this pack's local skill edits separate from upstream source.

