# Legacy Case Engine Retirement

The case engine was retired in version 1.0 and is not lifecycle authority.

## What was retired

- legacy case registration, transition, approval, STOP mutation, review,
  repair, closure, and publication mutation
- former case-runtime controller, supervisor, broker, and actor-binding
  lifecycle authority
- session-state and current-state equality gates
- active-slice, handoff, path-only, and anti-loop permission authority
- installed direct mutation commands and rules that allowed them
- review or repair generation outside the approved campaign graph

## Why it was retired

The former design distributed lifecycle decisions across Git files, session
records, hooks, adapters, controller state, caller-declared roles, and runtime
commands. Those sources could age independently. A stale record could disagree
with the exact repository head or verified evidence and then produce a false
stop, a false permission, an ambiguous review result, or another support loop.

Version 1.0 replaces that fragmented authority with one immutable campaign
contract, one pure reducer, one external SQLite store, exact revisions and
fencing epochs, durable cancellation, trusted validation, and a reconciled
external-effect outbox. Repository documents and adapters can report facts but
cannot perform lifecycle transitions.

## Permanent compatibility boundary

`scripts/agent/case_state.py` is a permanent compatibility-denial stub. Every
former command returns `LEGACY_ENGINE_RETIRED` with exit code 78. No command can
register, transition, approve, stop, review, repair, publish, or reactivate a
legacy case.

The stub exists only to give stale callers a deterministic, explicit failure.
It is not a compatibility engine, shadow engine, fallback mode, or second
lifecycle authority.

## Preserved evidence

Read-only inspection and archive verification live in
`scripts/agent/campaign_engine/legacy.py`. Legacy records are preserved as
evidence and never imported as active campaign state. Unresolved records remain
`LEGACY_ARCHIVED_UNRESOLVED`, and no archived record is translated into success
or failure without evidence. Associated Git branches and worktrees are not
modified by archival.

The proven exact-file replacement verification semantics also remain, but only
inside the campaign engine's narrow effect driver. They cannot select work or
change lifecycle state.

Use [Campaign Engine Contract](campaign-engine.md) for the current system.
