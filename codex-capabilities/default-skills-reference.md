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
3. Chrome
4. Computer Use
5. Cloudflare
6. Build iOS Apps
7. Test Android Apps
8. Documents
9. Document Skills
10. Spreadsheets
11. Presentations
12. Data Analytics
13. Build Web Data Visualization
14. Product Design
15. Understand Anything
16. Cloudflare

## Why managed capabilities stay managed

Do not copy Codex-managed plugin installation files into this repo.

Plugin-managed skills can change with Codex releases, rely on connector permissions, or call MCP servers and app integrations. Installing them through Codex keeps the capability boundary clear.

## Activation steps

1. Install this repo with `scripts/install.ps1`.
2. Restart Codex.
3. Open Codex Plugins.
4. Install the plugins listed above.
5. Connect only the services needed for the project.
6. Restart Codex again.
7. Open a new chat.
8. Paste `templates/first-codex-prompt.md`.

## Verification note

Codex behavior changes over time. Before a public release, re-check current OpenAI Codex documentation for plugin, skill, MCP, and hook behavior.
