# ADR 0001: Brokered Runtime Action Boundary

- Status: Historical, fully superseded by the campaign engine replacement
- Decision owners: Human run-envelope authority and Codex Coding OS maintainers
- Scope: Canonical review receipts, terminal quarantine, and one exact existing-file replacement primitive

This record is retained as historical design evidence only. None of its case,
controller, supervisor, broker, grant, or lifecycle components are installed or
callable. The campaign reducer, SQLite store, native host, and exact-file effect
driver replace the design completely.

ADR 0002 previously replaced this ADR's App Server identity, actor, thread, turn, and
supervisor-HMAC authorization design. Existing `ccos-runtime-action-grant-v1`
records remain readable only for bounded terminal recovery. New v1 grant
issuance is disabled. The reviewer-receipt and terminal-quarantine decisions in
this ADR remain current.

## Context

Codex App Server can expose native parent and child thread identities, but the
failed feasibility cases did not prove that its approval surface can enforce
one canonical case at the real mutation boundary. The prior design also placed
spawn-permit authority in an administrative driver instead of proving a
runtime parent-to-broker authorization channel. Adding another App Server tool
channel would recreate the same dependency and expand the model-visible attack
surface.

Three lifecycle problems remain independent of that product limitation:

1. Findings could freeze before every declared reviewer proved completion.
2. An erroneous successful closure had no narrow audited terminal correction.
3. Native thread identity was evidence, but not enforceable operating-system
   authority for one exact write.

## Decision

Keep Codex App Server proposal-only. Run every model parent and child read-only,
with network disabled, `approvalPolicy=never`, `dynamicTools=[]`, no MCP
servers, no hooks, no inherited tool environment, and defensive denial of all
native mutation requests. Use native collaboration and `thread/read` only to
corroborate identities assigned by a deterministic controller.

Move write authority to a reviewed deterministic supervisor and a separate
Windows broker principal. The supervisor holds a fresh HMAC key only in memory,
records one durable generation-attempt claim immediately before App Server,
seals the exact completed proposal receipt only after both sanitized live
transport records prove process-tree closure, issues one canonical action grant,
and launches the fixed broker entrypoint under the configured broker principal.
The broker accepts no arbitrary command, content, role, or target input. All
action details come from the protected canonical grant.

The initial primitive replaces one existing tracked file with exact presealed
bytes. It requires source pins, stable file identities, one hard link, exact
Git identity, a clean base head, distinct worker and broker SIDs, protected-root
ownership, complete recursive worker DENY rules, live denial probes, a
non-idempotent canonical claim, a protected hash-chain journal, atomic replace,
exact status verification, and rollback plus case lock on failure.

Treat access-control lockdown as one crash-recoverable transaction. Persist the
original descriptors and a sealed lockdown intent before the first descriptor
change. Keep lockdown active through post-replacement probes and canonical
completion. Restore parents before children and accept partial-restoration
recovery only when each descriptor is exactly original or exactly locked down.
The probes use real Windows delete, rename, and replace system calls against
sacrificial anchors under both worker profiles before issuance and after the
replacement.

The lifecycle engine also requires immutable declared reviewer assignments and
one exact `COMPLETED` receipt per reviewer before findings freeze. It provides
one exact `CLOSED_SUCCESS` to `CASE_LOCKED` quarantine operation bound to the
case revision and record hash. Quarantine preserves counters and records backup
and external-reconciliation evidence.

## Trust boundary

Trusted computing base:

- canonical `case_state.py`
- proposal-only `case_app_server_controller.py`
- deterministic `case_runtime_supervisor.py`
- one-shot `case_runtime_broker.py`
- their pinned install-bundle manifest entries
- the broker-owned canonical state and broker journal
- the Windows ownership and DACL configuration for target, state, source, and
  a dedicated nonoverlapping proposal root

Untrusted for authorization:

- model text and model-provided roles
- App Server approval behavior
- parent and child mutation attempts
- shell environment inherited from the user session
- prose state, handoffs, prompts, or repository mirrors

## Failure semantics

- Missing, failed, or incomplete reviewer receipts block findings freeze.
- Parent, reviewer, closure, incomplete, unknown, or forged actors cannot
  receive the implementation grant.
- Any unrelated mutation while a grant is active is denied without revision
  change.
- Any preclaim verification failure consumes the issued grant and locks the
  exact case.
- A claimed grant may recover once only from protected journal evidence and an
  exact baseline. Exact replacement may complete without another write.
- Any mismatched target, source, identity, revision, ACL, receipt, proposal,
  journal, or second recovery causes rollback and `CASE_LOCKED`.
- Supervisor loss destroys the only HMAC key context. A later trusted
  supervisor consumes any persisted active grant as
  `SUPERVISOR_CONTEXT_LOST`. It never reconstructs, releases, or reissues it.
- A pre-grant crash with a matching lockdown intent restores the exact snapshot,
  terminally consumes the implementation generation with
  `PREISSUE_GENERATION_ABANDONED`, and locks the case. A no-grant case in any
  state other than `IMPLEMENTING` cannot start App Server.
- Any claimed generation attempt that does not reach canonical grant issuance
  is aborted and locks the exact case. A rerun resolves that claim before schema
  inspection and cannot start a second App Server generation.
- A completed grant with a missing broker completion record may reconstruct
  that record only from the exact canonical result and post-isolation digest,
  then restore access control. An active orphan rolls back, fails, and locks.
- Worker `auth.json` must remain absent when absent initially. A pre-existing
  file remains operator-owned, byte-identical, unlogged, and undeleted.
- Erroneous closure quarantine never reopens a case or resets limits.

## Consequences and limits

This design removes App Server approval behavior from the write trust boundary
and preserves native parent-child automation for read-only proposal and review
work. It also gives every accepted write an exact canonical and operating-system
audit chain.

The tradeoff is narrow coverage. This decision does not authorize arbitrary
commands, new files, deletions, renames, multi-file patches, commits, pushes,
pull requests, publication, deployment, credentials, or universal
synchronization. General coding needs separately designed primitives and must
not inherit authority from this acceptance case.

## Rejected alternatives

- App Server approval as the enforcement boundary. Rejected because live cases
  did not prove canonical authorization at that boundary.
- Parent-to-broker dynamic tool or spawn-permit channel. Rejected because it
  expands the model-visible authority surface and repeats the failed control
  dependency.
- Administrative preissued permits. Rejected because they do not prove runtime
  parent authorization and can bypass the intended boundary.
- Persisted controller HMAC key in the case root. Rejected because worker read
  denial and secret continuity were not independently established.
- General-purpose privileged helper. Rejected because command, path, or content
  parameters would create a broad mutation bypass.

## Acceptance requirement

Production adoption requires a real Windows parent-child scenario under the
separate worker and broker principals. It must prove native identity
corroboration, parent and reviewer mutation denial, unknown and incomplete child
denial, one exact implementer replacement, second-action denial, stale-revision
denial, App Server and broker restart behavior, supervisor-loss locking, worker
root and nested ACL probes, one terminal broker audit record, and no enabled
mutation bypass.
