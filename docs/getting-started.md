# Getting Started

This guide takes you from a downloaded copy of Codex Coding OS to the first project-planning chat.

## Five-Minute Setup

### Windows

1. Download or clone this repository.
2. Open the repository folder in File Explorer.
3. Click the address bar, type `powershell`, and press Enter.
4. Run:

```powershell
$ExpectedBundleSha256 = (Get-Content -Raw -LiteralPath .\install-bundle.manifest.json | ConvertFrom-Json).aggregate_sha256
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install.ps1 -ExpectedBundleSha256 $ExpectedBundleSha256 -ArchiveMode
```

The installer verifies every bundled file against `install-bundle.manifest.json` and
requires this aggregate SHA-256 value. For a release download, compare the value
to the publisher-provided release checksum before running the command.

5. Restart Codex.
6. Open a new Codex chat.
7. Open `templates/first-codex-prompt.md`, replace `{{write_the_idea_here}}`, and paste the prompt into Codex.

### macOS or Linux

1. Open Terminal in the repository folder.
2. Run:

```bash
python_cmd="$(command -v python3 || command -v python)"
expected_bundle_sha256="$("$python_cmd" -c 'import json; print(json.load(open("install-bundle.manifest.json", encoding="utf-8"))["aggregate_sha256"])')"
chmod +x ./scripts/install.sh ./scripts/uninstall.sh
./scripts/install.sh --expected-bundle-sha256 "$expected_bundle_sha256" --archive-mode
```

3. Restart Codex.
4. Open a new Codex chat.
5. Open `templates/first-codex-prompt.md`, replace `{{write_the_idea_here}}`, and paste the prompt into Codex.

## Confirm the Install

### Windows

```powershell
Test-Path "$HOME\.agents\skills\codex-coding-os-master\SKILL.md"
Test-Path "$HOME\.codex\coding-os\templates\first-codex-prompt.md"
```

Each command should return `True`. The normal archive route does not change
global Codex policy files.

### macOS or Linux

```bash
test -f "$HOME/.agents/skills/codex-coding-os-master/SKILL.md" && echo "Master skill installed"
test -f "$HOME/.codex/coding-os/templates/first-codex-prompt.md" && echo "Support files installed"
```

The normal archive route does not change global Codex policy files.

## One-Time Legacy Nested-Skills Migration

Use this path only when an existing Codex Coding OS v2 ownership manifest already
records its managed skills under `$HOME\.codex\skills`. It is not a general
override for custom paths.

```powershell
$ExpectedBundleSha256 = (Get-Content -Raw -LiteralPath .\install-bundle.manifest.json | ConvertFrom-Json).aggregate_sha256
$LegacySkillsRoot = "$HOME\.codex\skills"
.\scripts\install.ps1 -ExpectedBundleSha256 $ExpectedBundleSha256 -ArchiveMode -SkillsRoot $LegacySkillsRoot -CodexHome "$HOME\.codex" -LegacyOverlapMigration
```

The option accepts only that exact nested layout. It stops if the v2 ownership
manifest is missing, malformed, or records different roots. A v2 manifest names
skill roots only, so the migration does not treat nested files as proven owned.
Before changing any recorded v2 skill, it checks its descendants against the
incoming source skill tree. It stops before live mutation when a descendant is
not in that incoming tree, or when a recorded v2 skill is no longer bundled.
The transaction preserves non-managed files in `.codex\skills` and does not move
or overwrite `.agents\skills`.

Use `-LegacyOverlapMigration` again for a later update or uninstall that targets
the same nested layout:

```powershell
.\scripts\uninstall.ps1 -SkillsRoot "$HOME\.codex\skills" -CodexHome "$HOME\.codex" -LegacyOverlapMigration
```

If separately authorized universal policy synchronization is part of that operation,
pass `-UniversalBundleId` with the bundle identifier bound to the current closed
authority case. The default remains the standard policy bundle identifier. A prior
case or blank identifier is not accepted.

## What the First Chat Should Produce

Codex should start with project intake and consolidated material-decision questions. It should create a workflow manifest before controlled documents, stop when important decisions are unresolved, and wait for explicit approval before coding. It must not jump directly into the seven documents while material decisions remain open.

Expected workflow:

1. Project scope and source inventory.
2. Consolidated material-decision questions.
3. Project brief and seven controlled source documents only after material decisions are resolved.
4. Technical design document and relevant ADRs.
5. Repo documentation, AGENTS instructions, current delivery state, active-slice manifest, and session-start gate.
6. Persistent handoff and validation report.
7. First bounded implementation slice only after manifest and user approval.

## Session Start And Resume

Projects prepared by the full workflow include a live `docs/delivery/current-state.md` file, `docs/delivery/active-slice-manifest.json`, and a project-local session continuity command. The packaged helper is `.agents/skills/project-session-continuity/scripts/session_continuity.py`.

When working directly in this source checkout, run the packaged helper at the start of every new or resumed non-trivial session:

```text
python .agents/skills/project-session-continuity/scripts/session_continuity.py start --profile auto --start-new
```

The command reports Git and coordination state, requires incoming work inspection, blocks dirty new-session starts, and blocks an implementation next action when either manifest does not permit coding.

During project setup, copy that same helper to the target project's `scripts/agent/session_continuity.py` before using the project-local command. There is no separate source-repository wrapper at that path.

The helper supports two repository profiles. The `product` profile uses project
delivery files. The strict `coding-os-source` profile is for this public source
repository and reads Git and pack identity without requiring or creating product
delivery files. `auto` selects the source profile only when the complete Coding OS
sentinel set is present. A partial or malformed set blocks instead of guessing.

To display reentry state without fetching, writing, creating a handoff, or
triggering review, run:

```text
python .agents/skills/project-session-continuity/scripts/session_continuity.py summary --profile auto --json
```

The summary is informational only. It does not permit coding, publication, or a
case lifecycle transition.

## Automation Prompt Families

Use `templates/sequential-manual-prompt.md` when the user will manually start each
next session. Use `templates/parent-orchestrator-prompt.md` only after the user
explicitly approves parent/orchestrator automation. Parent/orchestrator closeout
must run the review-state collector when present, re-check current PR head,
current-head inline comments, issue comments, required checks, local branch state,
stale-closeout status, and publication stabilization typed states before reporting
facts to the canonical case-state engine. The continuity helper and review prose
cannot approve or close a case.

## Use A Lighter Workflow For Small Changes

The full project workflow is for new products, unclear repositories, material architecture or product decisions, shared behavior, sensitive data, and other work where a wrong assumption has meaningful cost.

For a small, reversible, already-specified change, use the narrowest matching skill and proportionate validation. The Stage 0 to Stage 6 documentation model remains available under the installed `new-project-documentation-system` skill when a project needs deeper control.

## Starting from an Existing Repo

Open Codex from the existing repository folder and use the first prompt. Replace the idea section with:

```text
This is an existing repository. Inspect the repo and current documentation first. Do not change code yet. Identify the current state, missing decisions, documentation gaps, and the smallest safe next implementation slice.
```

Codex should read the repository and its `AGENTS.md` files before proposing edits.

## Optional External Skill

The pack works without external skill installation. To install the optional pinned upstream skill and apply the Coding OS overlay:

```powershell
.\scripts\install-external-skills.ps1 -Install forrestchang-andrej-karpathy-skills -ApplyOverlays
```

## Troubleshooting

### Codex does not detect the skills

Confirm the master skill exists under `$HOME\.agents\skills\codex-coding-os-master\SKILL.md`, restart Codex, and explicitly start the first prompt with:

```text
Use $codex-coding-os-master.
```

### You do not want global AGENTS rules

The normal archive route already leaves global Codex policy unchanged. Use a project-level `AGENTS.md` and explicitly invoke `$codex-coding-os-master` when needed.

### An external install is blocked

The external installer blocks unpinned installable sources by default. Review `external-skills/manifest.json` and `docs/external-skills-installation.md`. Do not bypass the block unless you reviewed the source.

### Validate the downloaded pack

On Windows:

```powershell
.\scripts\validate-pack.ps1
.\tests\install-uninstall-smoke.ps1
```

On macOS or Linux:

```bash
bash -n ./scripts/install.sh ./scripts/uninstall.sh ./tests/install-uninstall-smoke.sh
bash ./tests/install-uninstall-smoke.sh
```

## Separately Authorized Universal Policy Synchronization

Normal archive installation deliberately does not modify `$HOME\.codex\AGENTS.md` or the default Codex rules. Universal policy synchronization is a separately authorized operation, not setup guidance. Archive mode rejects it. A source checkout must provide both the verified bundle aggregate and the exact expected Git commit, and the operation also requires its own authorized closed case and authority record. See `docs/case-state-contract.md`; do not add a universal-policy flag to a routine install command.
