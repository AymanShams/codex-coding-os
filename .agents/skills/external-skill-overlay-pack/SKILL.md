---
name: external-skill-overlay-pack
description: Use when installing, referencing, patching, or adapting third-party skills without losing local edits, license attribution, or upstream separation.
---

# External Skill Overlay Pack

Use this skill when third-party skills are involved.

Use `external-skills/manifest.json` and `docs/external-skills-installation.md` as the source of truth for external repositories, install treatment, and overlays.

## Policy

Do not blindly copy third-party skill repos into this pack.

Use one of four treatments:

1. **Bundled**: only for original pack content or reviewed compatible license content.
2. **Overlay**: local improvement file placed beside an installed external skill.
3. **Reference**: link to the external repo and describe when to use it.
4. **Avoid**: skip because it overlaps, is too broad, is Claude-specific, or has trust risk.

## Overlay Workflow

1. Identify external skill repo and license.
2. Install external skill using its own instructions if the user chooses.
3. Keep upstream files unchanged.
4. Add a companion overlay file that records local rules.
5. Record the overlay in `THIRD_PARTY_SKILLS.md`.
6. Reapply overlays with `scripts/apply-external-skill-overlays.ps1`.
7. If this pack is the install vehicle, use `scripts/install-external-skills.ps1` with an explicit `-Install <source-id>` argument.

## Decision Rules

- Installed default Codex capability beats a new repo.
- A focused local skill beats a broad prompt pack.
- Reference-only is correct for catalogs and pattern libraries.
- Avoid is correct when provenance, license, security, or overlap risk dominates.
- Do not install reference-only or broad security packs automatically.

## Output

When reviewing an external skill, report:

- what it does
- whether it is skill, plugin, MCP, CLI, app, or prompt pack
- license status
- overlap with this pack
- install, overlay, reference, or avoid
- exact overlay files if any
