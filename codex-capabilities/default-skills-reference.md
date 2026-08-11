# Codex default and managed skills reference

This repo bundles local coding skills directly. Codex default skills and plugin-managed skills remain managed by Codex.

That keeps authentication, connector permissions, MCP configuration, updates, and trust prompts in the system that owns them.

## Included directly

| Category | Location |
|---|---|
| Full local coding skills | `.agents/skills/` |
| Local templates | `templates/` |
| External overlays | `patches/external-skills/` |
| Software technical docs reference | `.agents/skills/artifact-system-designer/references/software-technical-docs-pack.md` |
| Optional command approval rules | `.codex/rules/default.rules` |

## Codex default skills

These are referenced, not copied.

| Skill | Use |
|---|---|
| `skill-installer` | Install skills from repos, folders, or archives |
| `skill-creator` | Create and improve reusable skills |
| `plugin-creator` | Create Codex plugin bundles |
| `openai-docs` | Use official OpenAI and Codex documentation |
| `imagegen` | Generate visual assets when a software project needs them |

## Plugin-managed skills

The full managed skill list is tracked in `codex-capabilities/plugins.manifest.json`.

Install these plugins first for a normal web app:

1. Build Web Apps
2. GitHub
3. Browser
4. OpenAI Developers
5. Codex Security
6. Superpowers

Install these when the project uses the matching platform or workflow:

1. Vercel
2. Supabase
3. Neon Postgres
4. Chrome
5. Computer Use
6. Cloudflare
7. Build iOS Apps
8. Test Android Apps
9. Documents
10. Document Skills
11. Spreadsheets
12. Presentations
13. Data Analytics
14. Build Web Data Visualization
15. Product Design
16. Understand Anything

## Security capability boundary

Codex Security contributes 13 managed skills covering diff, standard, and deep
scans plus discovery, threat modeling, triage, validation, attack paths, fixes,
hardening, policy definition, vulnerability reports, and approved finding
tracking. This repository records those capabilities but does not copy them.

Supabase and Neon Postgres also remain plugin-managed. Install only the provider
used by the project. Generic PostgreSQL work uses the bundled
`postgres-security-best-practices` skill without assuming a provider connector.

See [Security Capability Operating Model](../docs/security-capability-operating-model.md)
for the exact skill map and fallback rules.

## Why managed capabilities stay managed

Do not copy Codex-managed plugin installation files into this repo.

Plugin-managed skills can change with Codex releases, rely on connector permissions, or call MCP servers and app integrations. Installing them through Codex keeps the capability boundary clear.

The source under `capability-routing/` is also not an installer for these
capabilities. It is dormant repository reference source. The public installer
does not register it or replace universal routing state.

## Activation steps

1. Install this repo with `scripts/install.ps1`.
2. Restart Codex.
3. Open Codex Plugins.
4. Install the plugins listed above.
5. Connect only the services needed for the project.
6. Review `docs/mcp-review-checklist.md` before enabling MCPs with sensitive access.
7. Review `docs/codex-rules.md` before copying command approval rules.
8. Restart Codex again.
9. Open a new chat.
10. Paste `templates/first-codex-prompt.md`.

## Verification note

Codex behavior changes over time. Before a public release, re-check current OpenAI Codex documentation for plugin, skill, MCP, and hook behavior.
