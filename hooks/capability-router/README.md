# Capability Router Support

This support package provides conservative `UserPromptSubmit` routing hints and
the local capability index used by `catalogue-router`.

The installer copies this folder as a support item. It can also refresh the
capability index with `-RefreshCapabilityIndex` on Windows or
`--refresh-capability-index` on macOS/Linux. The installer does not enable the
runtime hook automatically because hook activation changes Codex prompt context.
Enable the hook only after reviewing current Codex hook syntax and the source
files in this folder.

## What It Does

- Builds a local capability index from bundled skills, user skills, cached plugin
  skills, configured MCP names, and the bundled capability catalogue.
- Applies optional canonical registry metadata from
  `capability-index/canonical-registry.sample.csv` and
  `$CODEX_CAPABILITY_INDEX_DIR/canonical-registry.csv` when present.
- Adds prompt-time routing hints for likely skills or plugins.
- Separates primary family ownership from supporting families. Primary families
  suggest the workflow owner. Supporting families suggest additional skills to
  consider when they materially improve evidence, critique, validation, risk
  control, source access, tool access, or output quality.
- Separates source/data-access tools from skills. MCPs and other source tools can
  be required for evidence access without becoming primary or supporting skills.
- Suppresses noisy matches based only on generic terms such as `file`, `edit`,
  `skill`, `plugin`, `issue`, `workflow`, `that`, or `why`.
- Guards common false positives for document, presentation, spreadsheet, PDF,
  browser, React Native, Zoom, pre-mortem, and finance capabilities unless the
  prompt contains matching domain language.
- Treats generated hints as candidates. They must not override system/developer
  instructions, the latest user request, global `AGENTS.md`, project `AGENTS.md`,
  source-of-truth rules, safety gates, no-edit limits, or validation standards.

## Commands

```powershell
py -3 -B hooks\capability-router\capability_index_cli.py --refresh
py -3 -B hooks\capability-router\capability_index_cli.py --query "which skill should route this task"
py -3 -B hooks\capability-router\test_capability_router.py
.\scripts\install.ps1 -RefreshCapabilityIndex
./scripts/install.sh --refresh-capability-index
```

## Environment Overrides

| Variable | Default |
|---|---|
| `CODEX_CODING_OS_ROOT` | Two directories above this hook folder |
| `CODEX_HOME` | `$HOME\.codex` |
| `AGENTS_HOME` | `$HOME\.agents` |
| `CODEX_CAPABILITY_CATALOGUE` | Bundled `catalogue-router` capability catalogue |
| `CODEX_CAPABILITY_INDEX_DIR` | `$CODEX_HOME\coding-os\capability-index` |
| `CODEX_CAPABILITY_REGISTRY` | `$CODEX_CAPABILITY_INDEX_DIR\canonical-registry.csv` |

## Safety Position

This hook is fail-open because it only adds advisory context. It must not block a
user request, mutate files, install capabilities, or replace project validation.

## Registry Shape

The registry schema is in `capability-index/canonical-registry.schema.json`. The
sample registry is intentionally public-safe and incomplete. Users can maintain a
local complete registry under `$CODEX_CAPABILITY_INDEX_DIR/canonical-registry.csv`
or point `CODEX_CAPABILITY_REGISTRY` at another file.

Required columns:

- `name`
- `kind`
- `primary_families`
- `support_families`
- `all_families`
- `current_status`
- `installed_state`
- `decision`
- `canonical_path`
- `owner_source`
- `routing_role`
- `support_bias`
