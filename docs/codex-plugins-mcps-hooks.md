# Codex plugins, MCPs, hooks, and default capabilities

Codex plugins, MCP servers, and default skills should be installed through Codex instead of copied into this repo.

That keeps authentication, connector permissions, updates, and trust prompts in the right place.

## Included directly in this repo

| Item | Status |
|---|---|
| Full local coding skills | Bundled under `.agents/skills` |
| Local templates | Bundled under `templates/` |
| Local external overlays | Bundled under `patches/external-skills/` |
| `software-technical-docs-pack.md` | Bundled inside `.agents/skills/artifact-system-designer/references/` |
| Codex default and managed skills reference | Tracked in `codex-capabilities/default-skills-reference.md` and `codex-capabilities/plugins.manifest.json` |

## Managed through Codex

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

## Recommended plugin install list

Install these through Codex plugin browsing or workspace-approved plugin management.

### Core for a first web app

1. Build Web Apps
2. GitHub
3. Browser
4. OpenAI Developers
5. Codex Security
6. Superpowers

### Platform-specific

1. Vercel
2. Supabase
3. Chrome
4. Computer Use
5. Cloudflare

### App-type-specific

1. Build iOS Apps
2. Test Android Apps
3. Documents
4. Document Skills
5. Spreadsheets
6. Presentations
7. Data Analytics

## Install steps

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

## MCP setup steps

1. Install this repo first.
2. Open a terminal that has the Codex CLI available.
3. Add the OpenAI developer docs MCP when OpenAI or Codex docs are needed:

```powershell
codex mcp add openaiDeveloperDocs --url https://developers.openai.com/mcp
codex mcp list
```

4. Add `context7`, `chrome-devtools`, `code-review-graph`, or `node_repl` only when the project needs them.
5. Restart Codex after changing MCP configuration.

## MCP policy

- Install only MCPs needed for the project.
- Prefer official or actively maintained MCPs.
- Review the source before adding MCPs with broad filesystem, shell, browser, or network access.
- Store sensitive integration settings outside repository files.
- Treat MCP tools as real execution boundaries, not harmless prompts.

## Hooks policy

Hooks are not enabled by default in this pack.

Use hooks only after checking current Codex hook behavior and reviewing the hook source.

Recommended hook uses:

1. Run `scripts/validate-pack.ps1` before publishing the pack.
2. Run release-safety scans before public release.
3. Run secret-pattern scans before commit.
4. Run frontend QA checks after UI changes.
5. Reapply external skill overlays after optional external skill installation.

## Hook safety rules

- Do not bypass hook trust for unknown hook sources.
- Do not run destructive hooks.
- Do not run hooks that upload source files or sensitive integration settings.
- Keep hooks local and auditable.
- Prefer hooks that validate, scan, or report instead of hooks that mutate code.

## Source notes

Current Codex behavior changes over time. Check official OpenAI Codex documentation before enabling hooks, MCPs, or plugins in a public release.

Official references checked while preparing this pack:

- OpenAI Academy, Codex Plugins and Skills: https://openai.com/academy/codex-plugins-and-skills/
- OpenAI Docs MCP: https://platform.openai.com/docs/docs-mcp
- OpenAI MCP safety notes: https://platform.openai.com/docs/mcp/
