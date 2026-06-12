# External skills installation and overlay guide

The bundled skills are the primary workflow. External repositories are optional references, and only one upstream skill pack has an install path in this repo.

Do not bulk-install external skill packs into the default Coding OS. Review the source, install only what is needed, and keep local changes in overlays.

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
| Addy Osmani Agent Skills | https://github.com/addyosmani/agent-skills | Reference only |
| Claude Build Workflow | https://github.com/rohunj/claude-build-workflow | Reference only |
| Claude Code Templates | https://github.com/davila7/claude-code-templates | Reference only |
| Supabase Agent Skills | https://github.com/supabase/agent-skills | Reference only |
| OpenAI Agents Python | https://github.com/openai/openai-agents-python | Reference only |

## Optional install

From the repo root:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install-external-skills.ps1 -List
.\scripts\install-external-skills.ps1 -Install forrestchang-andrej-karpathy-skills -ApplyOverlays
```

This clones the external source into `.external-sources/`, installs only the selected skill path when found, and applies this pack's overlay file.

## External source review and pinning

Every external source uses the same provenance fields in `external-skills/manifest.json`:

- `license`
- `reviewed_at`
- `pinned_commit`
- `sha256`
- `integrity_control` for installable sources
- `pin_status`

Reference-only sources are not installed by this script. The current installable source is pinned to a reviewed commit. Any future installable source must also be pinned before repeatable public release instructions are published.

If a future installable source is not pinned, the installer stops by default. After reviewing upstream, a user can explicitly install its current upstream state:

```powershell
.\scripts\install-external-skills.ps1 -Install forrestchang-andrej-karpathy-skills -ApplyOverlays -AllowUnpinned
```

This override is generic. It applies to any installable external source, not one specific source.

## Pinning an installable source

1. Review the upstream repository, license, and expected skill path.
2. Choose the exact commit to install.
3. Add that commit to `external-skills/manifest.json` as `pinned_commit`.
4. Add and verify `sha256` when installing from an archive or fixed artifact. For a Git checkout pinned and verified by exact commit, use `not-applicable-git-commit-pin`.
5. Set `pin_status` to `pinned-reviewed`.
6. Set `integrity_control` to `git-commit-pin` for a pinned Git checkout.
7. Run the install command without `-AllowUnpinned`.

## Manual install pattern

Use this only when an external source needs to be installed manually.

```powershell
git clone --no-checkout https://github.com/forrestchang/andrej-karpathy-skills .external-sources\forrestchang-andrej-karpathy-skills
git -C .external-sources\forrestchang-andrej-karpathy-skills fetch --depth 1 origin 2c606141936f1eeef17fa3043a72095b4765b9c2
git -C .external-sources\forrestchang-andrej-karpathy-skills checkout --detach 2c606141936f1eeef17fa3043a72095b4765b9c2
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
- Pin installable external sources before repeatable release instructions are published.
- Do not imply endorsement by the external authors.
- Do not claim Karpathy wording is verbatim.
- Keep this pack's local skill edits separate from upstream source.
