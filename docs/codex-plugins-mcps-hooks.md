# Codex Plugins, MCPs, Hooks, And Default Capabilities

## Direct Decision

This repo should not copy plugin-managed skill bodies from private plugin cache folders.

Install plugins through Codex. Keep plugin-managed skills, connectors, and Model Context Protocol servers under the Codex plugin and MCP systems so authentication, permissions, updates, and provenance remain controlled.

## Included Directly In This Repo

| Item | Status |
|---|---|
| Full local coding skills | Bundled under `.agents/skills` |
| Local templates | Bundled under `templates/` |
| Local external overlays | Bundled under `patches/external-skills/` |
| `software-technical-docs-pack.md` | Bundled inside `.agents/skills/artifact-system-designer/references/` |
| Codex default and managed skills reference | Tracked in `codex-capabilities/default-skills-reference.md` and `codex-capabilities/plugins.manifest.json` |

## Not Copied Directly

| Item | Treatment |
|---|---|
| Superpowers plugin skills | Install plugin in Codex |
| Codex Security plugin skills | Install plugin in Codex |
| Vercel plugin skills | Install plugin in Codex |
| Supabase plugin skills | Install plugin in Codex |
| Computer Use plugin skills | Install plugin in Codex |
| Browser plugin skills | Install plugin in Codex |
| Chrome plugin skills | Install plugin in Codex |
| Build Web Apps plugin skills | Install plugin in Codex |
| Build iOS Apps plugin skills | Install plugin in Codex when needed |
| Test Android Apps plugin skills | Install plugin in Codex when needed |
| Documents, Spreadsheets, Data Analytics plugin skills | Install plugin in Codex when needed |

## Recommended Plugin Install List

Install these through Codex plugin browsing or workspace-approved plugin management.

### Core For A First Web App

1. Build Web Apps
2. GitHub
3. Browser
4. OpenAI Developers
5. Codex Security
6. Superpowers

### Platform-Specific

1. Vercel
2. Supabase
3. Chrome
4. Computer Use
5. Cloudflare

### App-Type-Specific

1. Build iOS Apps
2. Test Android Apps
3. Documents
4. Document Skills
5. Spreadsheets
6. Presentations
7. Data Analytics

## Single-Click Style Install Steps

1. Open Codex.
2. Open Settings.
3. Open Plugins.
4. Search each plugin name.
5. Install the plugin.
6. Connect required apps only when needed.
7. Restart Codex after installing multiple plugins.
8. Open a new chat.
9. Paste `templates/first-codex-prompt.md`.

## Recommended MCPs

| MCP | Priority | Use |
|---|---|---|
| `openaiDeveloperDocs` | Recommended for OpenAI work | Current OpenAI, Codex, Apps SDK, and Agents docs |
| `context7` | Recommended | Current framework, SDK, and library docs |
| `chrome-devtools` | Recommended for frontend debugging | Browser inspection, screenshots, runtime debugging |
| `code-review-graph` | Optional for larger repos | Graph-backed dependency and impact analysis |
| `node_repl` | Optional for browser automation | Persistent JavaScript runtime |

## MCP Setup Steps

1. Install this repo first.
2. Open a terminal that has the Codex CLI available.
3. Add the OpenAI developer docs MCP when OpenAI or Codex docs are needed:

```powershell
codex mcp add openaiDeveloperDocs --url https://developers.openai.com/mcp
codex mcp list
```

4. Add `context7`, `chrome-devtools`, `code-review-graph`, or `node_repl` only when the project needs them.
5. Restart Codex after changing MCP configuration.

## MCP Policy

- Install only MCPs needed for the project.
- Prefer official or actively maintained MCPs.
- Do not add MCPs with broad filesystem, shell, browser, or network powers without reviewing the source.
- Do not put tokens in repo files.
- Store MCP secrets in the approved local credential store or environment secret manager.
- Treat MCP tools as real execution boundaries, not harmless prompts.

## Hooks Policy

Hooks are not enabled by default in this pack.

Use hooks only after checking current Codex hook behavior and reviewing the hook source.

Recommended hook uses:

1. Run `scripts/validate-pack.ps1` before publishing the pack.
2. Run private-term scans before public release.
3. Run secret-pattern scans before commit.
4. Run frontend QA checks after UI changes.
5. Reapply external skill overlays after optional external skill installation.

## Hook Safety Rules

- Do not bypass hook trust for unknown hook sources.
- Do not run destructive hooks.
- Do not run hooks that upload source files, secrets, or private data.
- Keep hooks local and auditable.
- Prefer hooks that validate, scan, or report instead of hooks that mutate code.

## Source Notes

Current Codex behavior changes over time. Check official OpenAI Codex documentation before enabling hooks, MCPs, or plugins in a public release.

Official references checked while preparing this pack:

- OpenAI Academy, Codex Plugins and Skills: https://openai.com/academy/codex-plugins-and-skills/
- OpenAI Docs MCP: https://platform.openai.com/docs/docs-mcp
- OpenAI MCP safety notes: https://platform.openai.com/docs/mcp/
