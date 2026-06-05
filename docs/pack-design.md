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

## Architecture

| Layer | Files | Purpose |
|---|---|---|
| Installer | `scripts/install.ps1` | Copies skills and optionally adds global AGENTS rules |
| Master skill | `.agents/skills/codex-coding-os-master/SKILL.md` | Routes the full coding workflow |
| Workflow skills | `.agents/skills/*/SKILL.md` | Full local skills for PRD, docs, repo instructions, QA, security, architecture, frontend, and review gates |
| Templates | `templates/*.md` | Gives the user and Codex controlled output shapes, including the full repo documentation pack template |
| Third-party references | `THIRD_PARTY_SKILLS.md`, `patches/` | Links external skills and stores local overlay notes |
| Validation | `scripts/validate-pack.ps1` | Checks required files, skill frontmatter, release exclusions, and secret patterns |

## Operating model

1. Install the pack.
2. Restart Codex.
3. Paste `templates/first-codex-prompt.md`.
4. Answer only the scope questions needed to define the project.
5. Create the controlled docs.
6. Create the technical design document.
7. Create repo instructions.
8. Start one bounded implementation slice.
9. Use the review, security, design, RCA, and validation skills when the task calls for them.
10. Validate before completion.

## Skill boundary

The full bundled inventory is documented in `docs/full-skill-inventory.md`.

The earlier abbreviated skeleton skills were removed. Full local skills now cover those responsibilities, including `new-project-documentation-system`, `ai-coding-discipline`, `technical-docs-pack`, `playwright`, and the security skills.

## Repository boundary

This repo is limited to reusable workflow assets. Project code, generated documentation, deployment settings, and connected-service configuration belong in the user's project repo.

## Maintenance rule

Future improvements should be written as portable rules or templates before being added here. Keep the reusable lesson, not the one-off example.
