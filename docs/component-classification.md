# Component Classification

## Bundled Original Components
| Component | Included as | Reason |
|---|---|---|
| First-project kickoff workflow | Skill and templates | Original generic workflow distilled from prior coding projects |
| Source-locked documentation workflow | Skill and templates | Prevents generic project docs and keeps PRD, TDD, and repo docs aligned |
| Repo technical documentation pack template | Template | Actual generic template from the local `technical-docs-pack` reference folder |
| AI coding discipline | Skill | Enforces small changes, read-before-write, validation, and no unrelated rewrites |
| Repo instruction workflow | Skill and templates | Produces root and scoped AGENTS files for future chats |
| Simplify review gate | Skill and checklist | Catches overbuilt changes and duplicated logic |
| Security prelaunch gate | Skill and template | Covers minimum viable security before release |
| Frontend QA gate | Skill and template | Ensures UI changes are actually checked |

## Default Codex Capabilities To Use When Available
| Capability | How this pack references it |
|---|---|
| Codex skills | Installed under `.agents/skills` |
| Project `AGENTS.md` | Templates and installer support |
| Built-in code editing and validation | Skills instruct Codex to inspect, patch, and run commands |
| Browser or frontend verification tools | Referenced only when available in the running Codex environment |

## Third-Party Components Not Bundled
| Component | Treatment |
|---|---|
| Public skill repositories | Link in `THIRD_PARTY_SKILLS.md` |
| Public AI coding rule sets | Link in `THIRD_PARTY_SKILLS.md` and capture local changes as overlays |
| Plugin-managed skills | Do not copy from private plugin cache |

## Excluded Material
| Material | Reason |
|---|---|
| Private company context | Not needed for a generic coding pack |
| Private project names | Avoids leaking user-specific work |
| Credentials and environment values | Security risk |
| Old chat transcripts | Too much private and irrelevant content |
| Machine-specific plugin cache | Not portable and may violate provenance |
