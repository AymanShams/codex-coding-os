---
name: codex-coding-os-master
description: Use for software implementation, review, validation, publication, installation, cancellation, or recovery through the single executable Coding OS campaign engine.
---

# Codex Coding OS Master

Use `scripts/agent/campaign_engine/cli.py` as the only automated lifecycle
authority. The canonical installed copy is under
`%USERPROFILE%\.codex\coding-os`; its state is under
`%USERPROFILE%\.codex\coding-os-state`.

For manual coding, follow the current task, repository sources, exact Git
identity, and project validation. For automated work, admit one immutable finite
campaign specification, obtain explicit approval, and run only the engine
command named in its receipt.

For non-trivial manual work, use the installed `catalogue-router` to query the
canonical manifest and ordered routing policy. Treat the resulting primary and
supporting skills as workflow selection only. They do not widen mutation,
provider-write, merge, publication, or universal-install authority. The
repository `capability-routing/` tree is dormant reference source and must never
be activated or copied into `CODEX_HOME` without separate authorization.

Use these public commands:

```text
campaign_engine/cli.py admit
campaign_engine/cli.py approve
campaign_engine/cli.py run
campaign_engine/cli.py status
campaign_engine/cli.py cancel
campaign_engine/cli.py reconcile
campaign_engine/cli.py doctor
campaign_engine/cli.py legacy inspect
```

Do not reproduce transitions, budgets, actor roles, review generations,
cancellation, or publication rules here. Do not use Git-tracked execution state
as authority. Legacy case commands are retired and must return
`LEGACY_ENGINE_RETIRED`.
