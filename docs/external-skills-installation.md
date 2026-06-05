# External skills installation and overlay guide

The bundled skills are the primary workflow. External repositories are optional references, and only one upstream skill pack has an install path in this repo.

Do not bulk-install external skill packs for a first project. Review the source, install only what is needed, and keep local changes in overlays.

## Why this matters

External skill packs can introduce license drift, duplicate instructions, unexpected tool behavior, and maintenance risk.

## External sources

| Source | Link | Treatment |
|---|---|---|
| Andrej Karpathy Skills | https://github.com/forrestchang/andrej-karpathy-skills | Optional install with overlay |
| Anthropic Skills | https://github.com/anthropics/skills | Reference only |
| MiniMax Skills | https://github.com/MiniMax-AI/skills | Reference only |
| GitHub Spec Kit | https://github.com/github/spec-kit | Reference only |
| AGENTS.md | https://github.com/agentsmd/agents.md | Reference only |
| ECC | https://github.com/affaan-m/ECC | Reference only |
| Anthropic Cybersecurity Skills | https://github.com/mukul975/Anthropic-Cybersecurity-Skills | Reference only |

## Optional install

From the repo root:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install-external-skills.ps1 -List
.\scripts\install-external-skills.ps1 -Install forrestchang-andrej-karpathy-skills -ApplyOverlays
```

This clones the external source into `.external-sources/`, installs only the selected skill path when found, and applies this pack's overlay file.

## Manual install pattern

Use this only when an external source needs to be installed manually.

```powershell
git clone https://github.com/forrestchang/andrej-karpathy-skills .external-sources\forrestchang-andrej-karpathy-skills
New-Item -ItemType Directory -Force -Path "$HOME\.agents\skills" | Out-Null
Copy-Item .external-sources\forrestchang-andrej-karpathy-skills\skills\karpathy-guidelines "$HOME\.agents\skills\karpathy-guidelines" -Recurse -Force
.\scripts\apply-external-skill-overlays.ps1
```

## Overlay rule

Keep upstream files unchanged. Put local changes in an overlay file under `patches/external-skills/<source-id>/`.

## Current overlays

| Source | Overlay |
|---|---|
| `forrestchang-andrej-karpathy-skills` | `patches/external-skills/forrestchang-andrej-karpathy-skills/CODING_OS_OVERLAY.md` |

## Review before public release

- Re-check each external link.
- Confirm license compatibility.
- Do not imply endorsement by the external authors.
- Do not claim Karpathy wording is verbatim.
- Keep this pack's local skill edits separate from upstream source.
