# Pack Design

## Decision
This pack is a coding-only Codex operating system for first-project work. It bundles original generic skills, templates, scripts, and repo instructions. It does not bundle private project context or third-party skill bodies.

## Design Goals
- One install before the first Codex project chat.
- Idea to PRD to technical design to repo instructions to first slice.
- Beginner-friendly output with strict engineering discipline.
- Generic enough for public sharing after legal and license review.
- Source-faithful docs instead of generic AI filler.

## Architecture
| Layer | Files | Purpose |
|---|---|---|
| Installer | `scripts/install.ps1` | Copies skills and optionally adds global AGENTS rules |
| Master skill | `.agents/skills/codex-coding-os-master/SKILL.md` | Routes the first-project workflow |
| Workflow skills | `.agents/skills/*/SKILL.md` | Full local skills for PRD, docs, repo instructions, QA, security, architecture, frontend, and review gates |
| Templates | `templates/*.md` | Gives the user and Codex controlled output shapes, including the full repo documentation pack template |
| Third-party references | `THIRD_PARTY_SKILLS.md`, `patches/` | Links external skills and stores local overlay notes |
| Validation | `scripts/validate-pack.ps1` | Checks required files, skill frontmatter, private terms, and secret patterns |

## Operating Model
1. Install the pack.
2. Restart Codex.
3. Paste `templates/first-codex-prompt.md`.
4. Codex asks only necessary scope questions.
5. Codex creates the controlled docs.
6. Codex creates a technical design document.
7. Codex creates repo instructions.
8. Codex starts one bounded implementation slice.
9. Codex validates before completion.

## Full Skill Boundary

The full bundled inventory is documented in `docs/full-skill-inventory.md`.

The earlier abbreviated skeleton skills were removed. Their responsibilities are covered by full local skills such as `new-project-documentation-system`, `ai-coding-discipline`, `technical-docs-pack`, `playwright`, and the security skills.

## Exclusions
- Private company names.
- Client, private user, project, and production data.
- Local memory exports.
- Plugin cache contents from a private machine.
- Full third-party skill copies without separate license review.

## Maintenance Rule
If a future improvement comes from a private project, convert it to a generic rule before adding it here. Keep the cause, not the private example.

