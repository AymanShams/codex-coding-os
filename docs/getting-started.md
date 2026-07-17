# Getting Started

This guide takes you from a downloaded copy of Codex Coding OS to the first project-planning chat.

## Five-Minute Setup

### Windows

1. Download or clone this repository.
2. Open the repository folder in File Explorer.
3. Click the address bar, type `powershell`, and press Enter.
4. Run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install.ps1 -InstallGlobalAgents
```

5. Restart Codex.
6. Open a new Codex chat.
7. Open `templates/first-codex-prompt.md`, replace `{{write_the_idea_here}}`, and paste the prompt into Codex.

### macOS or Linux

1. Open Terminal in the repository folder.
2. Run:

```bash
chmod +x ./scripts/install.sh ./scripts/uninstall.sh
./scripts/install.sh --install-global-agents
```

3. Restart Codex.
4. Open a new Codex chat.
5. Open `templates/first-codex-prompt.md`, replace `{{write_the_idea_here}}`, and paste the prompt into Codex.

## Confirm the Install

### Windows

```powershell
Test-Path "$HOME\.agents\skills\codex-coding-os-master\SKILL.md"
Test-Path "$HOME\.codex\coding-os\templates\first-codex-prompt.md"
Select-String -Path "$HOME\.codex\AGENTS.md" -Pattern "BEGIN CODEX CODING OS"
```

Each command should return `True` or show the installed marker.

### macOS or Linux

```bash
test -f "$HOME/.agents/skills/codex-coding-os-master/SKILL.md" && echo "Master skill installed"
test -f "$HOME/.codex/coding-os/templates/first-codex-prompt.md" && echo "Support files installed"
grep -q "BEGIN CODEX CODING OS" "$HOME/.codex/AGENTS.md" && echo "Global rules installed"
```

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

Projects prepared by the full workflow include a live `docs/delivery/current-state.md` file, `docs/delivery/active-slice-manifest.json`, and a project-local session continuity command.

At the start of every new or resumed non-trivial project session, Codex should run:

```text
python scripts/agent/session_continuity.py start --profile auto --start-new
```

The command reports Git and coordination state, requires incoming work inspection, blocks dirty new-session starts, and blocks an implementation next action when either manifest does not permit coding.

The helper supports two repository profiles. The `product` profile uses project
delivery files. The strict `coding-os-source` profile is for this public source
repository and reads Git and pack identity without requiring or creating product
delivery files. `auto` selects the source profile only when the complete Coding OS
sentinel set is present. A partial or malformed set blocks instead of guessing.

To display reentry state without fetching, writing, creating a handoff, or
triggering review, run:

```text
python scripts/agent/session_continuity.py summary --profile auto --json
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

Reinstall without `-InstallGlobalAgents` or `--install-global-agents`. Use a project-level `AGENTS.md` and explicitly invoke `$codex-coding-os-master`.

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
