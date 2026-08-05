<!-- BEGIN CODEX CODING OS MANAGED: CAMPAIGN ENGINE POLICY -->
- Coding OS automation uses only `%USERPROFILE%\.codex\coding-os\scripts\agent\campaign_engine\cli.py` and the external SQLite campaign store. Run the executable command recorded by the approved campaign. Do not reconstruct lifecycle decisions in prose, hooks, skills, repository state, handoffs, branches, pull requests, or caller-declared roles.
- Manual coding remains governed by the current user request, repository sources, exact Git evidence, and project validation. Volatile current-state, active-slice, handoff, review, and stop fields in Git are informational only.
- `scripts/agent/case_state.py` and all former case, controller, broker, anti-loop, session-lifecycle, and publication commands are retired. A stale caller must receive `LEGACY_ENGINE_RETIRED`; it cannot reactivate or authorize the legacy engine.
<!-- END CODEX CODING OS MANAGED: CAMPAIGN ENGINE POLICY -->
