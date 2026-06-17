# Capability Router Hook Candidate

This optional hook candidate provides conservative `UserPromptSubmit` routing
hints. It is not enabled by the installer.

Use it only after reviewing current Codex hook syntax and the source files in
this folder.

## What It Does

- Builds a local capability index from bundled skills, user skills, cached plugin
  skills, configured MCP names, and the bundled capability catalogue.
- Adds prompt-time routing hints for likely skills or plugins.
- Suppresses noisy matches based only on generic terms such as `file`, `edit`,
  `skill`, `plugin`, `issue`, `workflow`, `that`, or `why`.
- Guards common false positives for document, presentation, spreadsheet, PDF,
  browser, React Native, Zoom, pre-mortem, and finance capabilities unless the
  prompt contains matching domain language.
- Treats generated hints as candidates. The `catalogue-router` skill remains the
  decision authority.

## Commands

```powershell
py -3 -B hooks\capability-router\capability_index_cli.py --refresh
py -3 -B hooks\capability-router\capability_index_cli.py --query "which skill should route this task"
py -3 -B hooks\capability-router\test_capability_router.py
```

## Environment Overrides

| Variable | Default |
|---|---|
| `CODEX_CODING_OS_ROOT` | Two directories above this hook folder |
| `CODEX_HOME` | `$HOME\.codex` |
| `AGENTS_HOME` | `$HOME\.agents` |
| `CODEX_CAPABILITY_CATALOGUE` | Bundled `catalogue-router` capability catalogue |
| `CODEX_CAPABILITY_INDEX_DIR` | `$CODEX_HOME\coding-os\capability-index` |

## Safety Position

This hook is fail-open because it only adds advisory context. It must not block a
user request, mutate files, install capabilities, or replace project validation.
