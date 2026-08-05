---
name: project-session-continuity
description: Use to inspect or continue work through the external Coding OS campaign store without treating repository state, handoffs, or session files as lifecycle authority.
---

# Project Session Continuity

Run the installed campaign engine `status` command for the exact campaign or
repository. The external SQLite store is authoritative. Repository
`current-state`, `active-slice`, handoff, review, next-action, and stop fields are
informational only and never permit or block coding.

The bundled `scripts/session_continuity.py` is a thin CLI client. Its read-only
commands delegate to `campaign_engine/cli.py`; legacy lifecycle mutations return
`LEGACY_ENGINE_RETIRED`.

Do not create a handoff, state-only pull request, successor campaign, review
generation, or repair generation from this skill. Use the current task in manual
mode or the immutable approved graph in automated mode.
