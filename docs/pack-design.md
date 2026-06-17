# Pack design

## Purpose

This pack gives Codex a full coding operating workflow: idea intake, PRD, technical design, repo instructions, implementation planning, bounded coding, design and documentation polish, security review, incident handling, RCA, and validation.

It bundles generic skills, templates, scripts, and repo instructions. External skills stay linked to their upstream sources unless this pack explicitly says otherwise.

## Design goals

- Install once before Codex starts serious software work.
- Move from idea or repo context to PRD, technical design, repo instructions, implementation, review, and validation.
- Keep the workflow clear for non-technical users without weakening engineering discipline.
- Make the repo suitable for public release after license and source review.
- Favor source-faithful docs over generic AI output.
- Preserve exact next action and workflow gates across new sessions.

## Architecture

| Layer | Files | Purpose |
|---|---|---|
| Pack manifest | `pack.manifest.json`, `pack.schema.json` | Source of truth for package version, required files, bundled skills, support items, release exclusions, and manifest shape |
| Installer | `scripts/install.ps1` | Copies skills and optionally adds global AGENTS rules |
| Master skill | `.agents/skills/codex-coding-os-master/SKILL.md` | Routes the full coding workflow |
| Workflow skills | `.agents/skills/*/SKILL.md` | Full local skills for PRD, docs, repo instructions, QA, security, architecture, frontend, and review gates |
| Templates | `templates/*.md` | Gives the user and Codex controlled output shapes, including the full repo documentation pack template |
| Third-party references | `THIRD_PARTY_SKILLS.md`, `patches/` | Links external skills and stores local overlay notes |
| Validation | `scripts/validate-pack.ps1`, `scripts/release-safety-scan.ps1` | Checks manifest inventory, skill frontmatter, release exclusions, forbidden files, secret patterns, and optional Git history scanner runs |
| Session continuity | `.agents/skills/project-session-continuity/` | Provides generic current-state, session-start, boundary-decision, and persistent-handoff controls |
| Parallel worktree lanes | `scripts/agent/worktree_lanes.py`, `docs/parallel-worktree-doctrine.md`, `templates/worktree-task-contract.md` | Offers, creates, validates, and closes bounded worktree lanes after manifest and user-approval gates pass |
| Command policy templates | `.codex/rules/` | Optional Codex command approval rules for destructive commands, installs, deployments, migrations, and secret exposure |
| CI | `.github/workflows/validate.yml` | Runs validation and install/uninstall smoke tests on Windows, Ubuntu, and macOS, then builds the package on Windows |

`pack.schema.json` documents the manifest reference shape. `scripts/validate-pack.ps1`
is the enforcement layer: it checks the manifest fields this pack depends on, plus
the presence and JSON validity of `pack.schema.json`.

## Operating model

1. Install the pack.
2. Restart Codex.
3. Paste `templates/first-codex-prompt.md`.
4. Answer only the scope questions needed to define the project.
5. Create the controlled docs.
6. Create the technical design document.
7. Create repo instructions.
8. Add current state and session continuity.
9. Start one bounded implementation slice.
10. Offer parallel worktree lanes only when the workflow manifest and active-slice manifest both permit coding and the task is material, high-risk, or naturally separable.
11. Use the review, security, design, RCA, and validation skills when the task calls for them.
12. Validate before completion.

## Skill boundary

The full bundled inventory is documented in `docs/full-skill-inventory.md`.

The earlier abbreviated skeleton skills were removed. Full local skills now cover those responsibilities, including `new-project-documentation-system`, `ai-coding-discipline`, `technical-docs-pack`, `playwright`, and the security skills.

## Repository boundary

This repo is limited to reusable workflow assets. Project code, generated documentation, deployment settings, and connected-service configuration belong in the user's project repo.

## Maintenance rule

Future improvements should be written as portable rules or templates before being added here. Keep the reusable lesson, not the one-off example.

Add a first-party skill only when it closes a demonstrated capability gap, prevents a recurring failure mode, or materially improves verification.

When a file, skill, or support directory is added or removed, update `pack.manifest.json` first, then update reader-facing docs.

## Release version policy

- `pack.manifest.json#version` is the sole package release version.
- Do not add a separate `VERSION` file or duplicate the release version in another machine-readable source.
- Use semantic versioning.
- Add a matching entry to `CHANGELOG.md` before release.
