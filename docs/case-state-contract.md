# Legacy Case Engine Retirement

The case engine was retired and is not lifecycle authority.

`scripts/agent/case_state.py` is a permanent compatibility-denial stub. Every
former command returns `LEGACY_ENGINE_RETIRED` with exit code 78. No command can
register, transition, approve, stop, review, repair, publish, or reactivate a
legacy case.

Read-only inspection and archive verification live in
`scripts/agent/campaign_engine/legacy.py`. Legacy records are preserved as
evidence and never imported as active campaign state.

Use [Campaign Engine Contract](campaign-engine.md) for the current system.
