# Codex Default And Managed Skills Reference

## Direct Decision

This repo bundles our local coding skills directly, but it does not copy Codex default skills or plugin-managed skills.

Codex-managed capabilities must stay managed by Codex because they can depend on account permissions, connector authentication, plugin updates, MCP server configuration, and local trust prompts.

## Included Directly

| Category | Location |
|---|---|
| Full local coding skills | `.agents/skills/` |
| Local templates | `templates/` |
| External overlays | `patches/external-skills/` |
| Software technical docs reference | `.agents/skills/artifact-system-designer/references/software-technical-docs-pack.md` |

## Codex Default Skills To Leave Managed

These are referenced, not copied.

| Skill | Use |
|---|---|
| `skill-installer` | Install skills from repos, folders, or archives |
| `skill-creator` | Create and improve reusable skills |
| `plugin-creator` | Create Codex plugin bundles |
| `openai-docs` | Use official OpenAI and Codex documentation |
| `imagegen` | Generate visual assets when a software project needs them |

## Plugin-Managed Skills To Install Through Codex

The complete managed skill list is tracked in `codex-capabilities/plugins.manifest.json`.

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

## Why They Are Not Copied

Do not copy plugin cache folders into this repo.

Reasons:

1. Plugin skills can change with Codex releases.
2. Some plugin skills rely on connector permissions.
3. Some plugin skills rely on MCP servers or app authentication.
4. Private cache provenance is weaker than an official plugin install.
5. A license may allow copying, but copying can still break update, trust, or credential boundaries.

## Activation Notes For A New User

1. Install this repo with `scripts/install.ps1`.
2. Restart Codex.
3. Open Codex Plugins.
4. Install the plugins listed above.
5. Connect only the services needed for the first project.
6. Restart Codex again.
7. Open a new chat.
8. Paste `templates/first-codex-prompt.md`.

## Verification Notes

Codex behavior changes over time. Before publishing a public release, re-check current OpenAI Codex documentation for plugin, skill, MCP, and hook behavior.
