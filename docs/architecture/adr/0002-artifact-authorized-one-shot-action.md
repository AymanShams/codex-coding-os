# ADR 0002: Artifact-Authorized One-Shot Action

- Status: Accepted
- Decision owners: Human run-envelope authority and Codex Coding OS maintainers
- Scope: One exact existing-file replacement enforced outside every model process
- Supersedes: ADR 0001 for production action authorization

## Context

Live App Server feasibility did not establish a dependable parent-to-broker
authorization boundary. Runtime parent and child identities, role labels,
thread identifiers, turn identifiers, approval messages, and model-visible
tool channels are application evidence. They are not operating-system write
authority. Making production authorization depend on that evidence would keep
the exact mutation boundary coupled to App Server behavior.

The required action is narrower. One case must be able to authorize one exact
replacement of one existing tracked file with already produced, predeclared
bytes. No model process needs write authority to do that. Proposal generation,
review, and closure can remain untrusted inputs to a deterministic capability
decision.

## Decision

Add protocol `ccos-proposal-action-grant-v2` to the existing canonical
`runtime.action_grants` map. V2 is actorless. The canonical record itself is a
one-use capability for one exact replacement. It does not name or trust an
implementer role, App Server thread, turn, collaboration path, approval result,
or controller-supplied role claim.

The grant binds all of the following before it becomes `ISSUED`:

- exact case identifier and expected case revision
- exact normalized repository, branch, and worktree
- exact full base-head commit SHA
- exact normalized existing target path
- exact target baseline SHA-256 digest
- exact normalized proposal artifact path, SHA-256 digest, and byte size
- exact replacement SHA-256 digest for the proposal bytes
- fixed operation `replace_existing_file_v1`
- exact broker security identifier, or SID
- absolute expiry
- pinned source and protocol identifiers required by the installed bundle

The proposal artifact is untrusted storage. The broker opens it only after
claiming the grant, rejects links and aliases, reads the exact declared byte
count, and requires its digest to equal both the proposal digest and replacement
digest bound by the grant. Those exact bytes are the complete action payload.
No caller may supply a command, target, content, patch, role, thread, or alternate
path at execution time.

Issuance requires the case to be at the exact expected revision and to have no
existing action grant. The case engine stores V2 in the same one-grant map and
uses the existing `ISSUED` to `CLAIMED` to `COMPLETED` or `FAILED` terminal
lifecycle. Claim is non-idempotent write authority only on the first successful
transition. A retry may read the existing claim result but cannot authorize a
second write. Expiry, revision drift, binding mismatch, or pre-action
verification failure terminally consumes the grant as `FAILED` and locks the
exact case.

The separate broker is the only process permitted to write the governed target.
At the actual replacement boundary it independently verifies the broker SID,
case and grant state, expected revision, repository identity, branch, worktree,
base head, target path, baseline digest, proposal identity, proposal bytes,
replacement digest, expiry, protected-root controls, and source pins. It then
atomically replaces the one existing file, verifies the exact post-action
digest and changed-path set, records completion, and denies replay. Failure
uses the existing protected journal, rollback, and case-lock behavior.

## Authority and trust boundary

Trusted for this action:

- the canonical case engine and protected `runtime.action_grants` record
- the fixed one-shot broker entrypoint and its pinned source
- broker-owned canonical state, proposal, baseline, and journal roots
- operating-system identity and access controls that exclude model processes

Not authority:

- App Server identity or approval behavior
- parent, child, reviewer, closure, unknown, or claimed agent roles
- thread, turn, task, session, or collaboration identifiers
- proposal-producing process identity
- model text, prose receipts, handoffs, prompts, or environment claims

Because no model role receives mutation authority, parent, reviewer, closure,
unknown, and incomplete processes do not need role-specific write exceptions.
They may produce or inspect proposal evidence, but only the broker can exercise
the exact capability recorded by the canonical engine.

## Compatibility

Existing `ccos-runtime-action-grant-v1` records remain readable so historical
state can be inspected and an already issued or claimed grant can reach one
bounded terminal recovery result. Recovery must preserve the original v1
verification, journal, rollback, and lock rules. It cannot release, reset,
convert, clone, or reissue a v1 grant.

New v1 issuance is disabled. All new production action grants use
`ccos-proposal-action-grant-v2`. V1 and v2 records share the existing
`runtime.action_grants` map and the one-grant-per-case limit. They do not share
authorization semantics, and v1 actor fields must never be inferred for v2.

## Failure semantics

- A missing, expired, already claimed, completed, or failed grant cannot write.
- A stale expected revision or any repository, branch, worktree, head, path,
  baseline, proposal, replacement, SID, expiry, or source-pin mismatch fails
  before replacement.
- A failed first claim or pre-action verification consumes the grant and locks
  the exact case. It never returns the grant to `ISSUED`.
- A crash after claim follows the protected-journal recovery rules. An exact
  replacement already present may be completed without another write. An exact
  baseline may be retried only by the bounded recovery path. Any third state
  rolls back and locks the case.
- Completion requires exactly one changed path and the exact replacement digest.
  A second execution or replay is denied without mutation.

## Consequences and limits

Production action authorization no longer depends on App Server parent-child
identity, role claims, approval routing, dynamic tools, or authentication
staging. The authorization boundary is deterministic and located at the exact
file replacement.

The primitive remains intentionally narrow. It does not authorize commands,
new files, deletions, renames, multi-file patches, commits, pushes, pull
requests, publication, deployment, credentials, or universal synchronization.
Those actions require their own explicit authority.

## Acceptance requirement

Acceptance must use an isolated temporary repository and prove that an exact V2
grant applies the declared proposal bytes once, changes only the bound path,
and survives restart as consumed. It must also prove denial without mutation
for missing or incomplete proposals, stale revision, wrong repository, branch,
worktree, head, target, baseline, proposal digest, replacement digest, broker
SID, expired grant, unknown callers, and replay. The acceptance audit must show
that no model, App Server, parent, child, reviewer, closure, or arbitrary tool
path has write access to the governed target.
