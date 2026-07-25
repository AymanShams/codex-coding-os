# Automation Case-State Contract

The case-state engine keeps automated implementation, review, repair, and
publication finite without disabling parent orchestration, child agents,
parallel work, or GitHub automation.

The canonical implementation is `scripts/agent/case_state.py`. Installed
product-repository adapters call it at
`~/.codex/coding-os/scripts/agent/case_state.py`. An adapter may honor
`CODEX_CASE_ENGINE_PATH` only as an explicit test override. The default data
root is `~/.codex/case-state`, outside the managed Coding OS installation.
Tests may pass `--state-root` to isolate their state.

Policy files, skills, prompts, and product-repository adapters must not copy the
transition graph or maintain a second counter. They call the canonical engine
and treat its JSON response as the machine authority.

## Stable identity and bindings

Every case has one lowercase canonical UUID. It is created explicitly. A new
chat, task, thread, branch, worktree, pull request, or session counter cannot
create a replacement case implicitly.

The single atomic store contains both cases and the binding registry.
Repository URLs are normalized, nonexclusive associations. Multiple cases may
therefore perform unrelated work in the same repository. `resolve` returns all
matching case identifiers for a repository and marks the result ambiguous when
more than one exists. It never silently chooses one case as the owner.

Branches are exclusive only as the exact normalized repository plus branch
pair. The same branch name may be used in different repositories, and different
branches may be used by different cases in the same repository. Worktree paths,
pull requests, thread or task identifiers, and universal bundle identifiers are
also exact exclusive bindings. An exclusive binding already owned by one case
cannot be rebound to another case.

Each mutation requires:

- a unique request or event identifier
- the exact expected case revision, or store revision for registration
- a payload that remains identical if the request is retried

An identical request is idempotent, which means that retrying it returns the
original result without applying the mutation twice. Reusing the identifier
with a different payload fails. A stale revision fails without changing state.

## Finite lifecycle

The only substantive lifecycle is:

```text
REGISTERED
  -> IMPLEMENTING
  -> CANDIDATE_FROZEN
  -> REVIEW_COLLECTING
  -> FINDINGS_FROZEN
       -> CLOSED_SUCCESS                         when there are no blockers
       -> REPAIR_AUTHORIZED                      when blockers exist
          -> REPAIR_COMPLETE
          -> CLOSURE_PREFLIGHT
          -> CLOSURE_CHECK
             -> CLOSED_SUCCESS
             -> CASE_LOCKED
```

The hard limits for one case are:

- one implementation generation
- one review cohort
- zero or one combined repair
- zero or one substantive closure check
- zero or one identical operational retry after a control failure

`CONTROL_FAILURE` is an operational state, not a substantive review state. It
preserves the prior state for one retry with the identical failure fingerprint.
A repeated control failure after that retry locks this exact case.

## Review and repair

`start-review` declares one immutable cohort before any finding is accepted.
Each required reviewer assignment binds a reviewer identifier, the
controller-bound native thread identifier, repository, exact reviewed head,
snapshot, exact scope string, and the SHA-256 digest of that UTF-8 string. The cohort cannot grow, shrink,
or change scope after collection begins.

Reviewers submit findings only while the case is `REVIEW_COLLECTING`. Every
finding requires a stable identifier, candidate, repository, exact reviewed
commit SHA, source, description, and one classification:

- `CURRENT_BLOCKER`
- `NON_BLOCKING`
- `INVALID_OR_STALE`
- `REDESIGN_REQUIRED`
- `CONTROL_FAILURE`

A finding for a commit other than the frozen review head is recorded as
`INVALID_OR_STALE`. It cannot authorize repair. The parent freezes the complete
finding set once, but only after every declared reviewer has submitted one
`ccos-review-completion-v1` receipt. A receipt is valid only when its reviewer,
native thread, repository, head, snapshot, exact scope, scope digest, finding
identifier set, native completion evidence, and request identifier match the
declared assignment. `FAILED` or `INCOMPLETE` is a recorded terminal cohort
result and cannot be treated as completion. Missing or non-completed receipts
block `freeze-findings` without changing the revision. Later findings remain
visible but are marked late and non-authorizing.

Repair requires an authority record and the exact full set of frozen
`CURRENT_BLOCKER` identifiers. The candidate head may advance only once, as the
single combined repair. Any other head drift locks only the affected case.

Closure preflight verifies the frozen review heads, repaired heads, authorized
blocker identifiers, exact repaired snapshot records, and absence of an
unapproved blocker. Identity failure leaves the case in `CLOSURE_PREFLIGHT` and
does not consume the closure check. The closure check can resolve only the
authorized blocker identifiers. It cannot add a new finding or become another
general review. A remaining blocker or repair regression locks the case.

Tool, network, reviewer, hash, or protocol failure becomes `CONTROL_FAILURE`.
It is not evidence that the product is defective.

## Case-scoped action guard

`action-check` uses protocol `ccos-case-action-v1`. Every response includes the
store schema version, case identifier and state, action, actor role, current
limits, normalized execution context, decision, stable reason code, and the
separate-authority flag.

The role and action separation is:

- `parent`: `case_administration` only
- `implementer_child`: `implementation` or `product_work`
- `review_child`: `review_collection` or the one `closure_check`
- `fix_child`: the one authorized `repair`
- `publication_child`: `publication`, plus boundary validation for `merge`,
  `deployment`, `release`, `credential_change`, and `universal_sync`

Child execution requires an associated repository and at least one exact
branch, worktree, pull request, thread, or universal bundle binding. Review,
repair, closure, and publication also require the exact canonical head for the
repository. Review uses the frozen review head. Repair, closure, and publication
use the current head, which becomes the repaired head after the one authorized
combined repair.

`action-check` evaluates one target case and an optional exact locked case. A
different UUID is not sufficient proof that work is unrelated. The guard
compares exact exclusive bindings and supplied context. An exact branch,
worktree, pull request, thread, universal bundle, or commit-head collision in
the same repository remains blocked. A shared repository association alone is
not an overlap, and an identical hash text in a different repository is not an
overlap. A lock on one case therefore does not block an unrelated branch or
unrelated product work in the same repository.

A global emergency stop is outside this case engine and is reserved for
credential compromise or uncontrolled concurrent mutation.

## Proposal-only App Server and runtime action boundary

The App Server is an identity and proposal surface, not a mutation authority.
Every parent, implementation, review, closure, incomplete, and unknown model
turn runs with a read-only sandbox, network disabled, `approvalPolicy=never`,
`dynamicTools=[]`, no selected capability roots, no MCP servers, no hooks, and
no inherited tool environment. The controller defensively declines every file
or command approval request, declines MCP elicitation, rejects every tool call,
and rejects unknown server requests. It never returns `acceptForSession`.

Native collaboration events establish only identity evidence. The trusted
controller correlates the sender thread, receiver thread, parent turn, agent
path, and native spawn activity, then corroborates each identity with
`thread/read`. A model-supplied role is never authority. Extra or ambiguous
children remain unknown and read-only.

An implementation child may return a strict `COMPLETED` proposal envelope in
its final turn. The deterministic controller binds the proposal digest and byte
count to the case, grant, native thread, native turn, operation identifier,
base head, target path, native evidence digest, timestamp, and nonce. The
supervisor normalizes the initial and restart capability records, sanitized
transport audits, process-tree closure, incomplete-child result, and stale
revision denial. Only after both App Server process trees close does it bind the
evidence digest into the receipt and grant and sign the receipt with an HMAC key
held only in supervisor memory. The key is not stored in the case root, passed
on the command line, written to logs, or inherited by App Server, Git, or
PowerShell helpers.

The trusted supervisor is the only component that composes the controller,
case engine, and separate-principal broker. It generates the in-memory key,
collects proposal evidence, issues the exact canonical grant, invokes the
broker with the key only in the broker process environment, verifies terminal
state, attempts one denied replay, and scrubs its key material. No supervisor
CLI accepts arbitrary commands, file content, model role claims, or an
unbounded mutation path.

The one-use broker is a deliberately narrow feasibility primitive. It supports
only an exact replacement of one already existing tracked file with presealed
bytes outside the model process. It is not a general coding executor and does
not prove arbitrary patch, command, create, delete, rename, multi-file, or
repository-wide mutation coverage.

Before issuance, the engine binds the exact implementer actor, thread and turn,
repository, branch, worktree, base head, target, baseline digest, replacement
digest, proposal identity, controller receipt digest, worker SID, broker SID,
expiry, and source pins. Baseline and replacement bytes are copied into the
protected state root and sealed by digest, size, device, file identifier, and
single-link count. The target, proposal, and sealed files may not be symbolic
links, reparse points, or hard-linked aliases. Baseline and replacement digests
must differ.

Windows enforcement requires distinct worker and broker principals. The target
root, canonical state root, broker source root, dedicated proposal root, and
each immediate parent are broker-owned. The proposal root may not overlap any
other protected root. Explicit worker DENY access control entries must cover write,
delete, delete-child, `WRITE_DAC`, and `WRITE_OWNER`. Recursive root denial must
carry both container and object inheritance with no `InheritOnly` or
`NoPropagateInherit` escape. A live worker probe must receive `ACCESS_DENIED`
for root creation, nested-descendant creation, exact-anchor overwrite, kernel
replace, rename, and delete access, actual hard-link creation, permission
change, and ownership takeover. Challenge-derived paths must remain absent,
and anchor hashes, stable identities, and security descriptor hashes must stay
unchanged. The broker must also prove it can write and clean up a fixed probe
under every protected root.

The access-control lifecycle is transactional and journaled. The broker records
`ACL_SNAPSHOT` and `ACL_LOCKDOWN_INTENT` before changing any discretionary
access control list, then records `ACL_LOCKDOWN_VERIFIED` only after both fixed
worker profiles pass. Lockdown remains in force through grant issuance, claim,
replacement, and the post-replacement dual-profile probes. Success must end in
the exact journal suffix `POST_ISOLATION_VERIFIED`, `COMPLETED`, then
`ACL_RESTORED`. Restoration applies parent descriptors before child
descriptors. A restart may continue a partial restoration only when every
current descriptor is either the exact original descriptor or the exact sealed
lockdown descriptor. Any third state fails closed.

The denial probes exercise actual Windows `DeleteFileW`, `MoveFileExW`, and
`ReplaceFileW` calls against broker-created sacrificial anchors in addition to
creation, overwrite, hard-link, access-control, and ownership probes. These
native calls run for the online App Server host profile and the offline model
sandbox profile before grant issuance and again after replacement. A policy
description or synthetic path comparison is not acceptance evidence.

While a grant is `ISSUED`, every unrelated case mutation is denied without a
revision change. Only claim or exact preclaim termination is allowed. While a
grant is `CLAIMED`, only exact completion or failure is allowed. A claim is
write authority only when its response contains `idempotent: false`. Revision
drift, an expired grant, a source-pin mismatch, a receipt mismatch, or a failed
preclaim check consumes the grant as `FAILED` and locks the exact case.

The protected `broker-journal` is the broker audit. It is hash chained and
single-instance locked. Preclaim, claim, replacement, completion, and failure
events record the broker SID and process identifier, grant and receipt hashes,
claim and result hashes where available, exact target path, pre-action and
post-action target hashes, and the exact changed path. Recovery permits one
restart from `CLAIMED` only when the target is still the exact baseline and the
protected journal proves the prior claim. An exact replacement already present
is completed without rewriting. Any other state rolls back atomically to the
sealed baseline and records `FAILED` plus `CASE_LOCKED`.

Restart continuity is intentionally bounded. App Server or broker processes
may restart while the trusted supervisor and its in-memory key remain alive.
Supervisor process loss destroys the key context. On the next trusted
supervisor startup, every persisted `ISSUED` or `CLAIMED` grant without that
live context is consumed with `SUPERVISOR_CONTEXT_LOST` and the exact case is
locked. It is never released, reissued, or reconstructed from a persisted
secret.

Startup recovery resolves durable state before schema inspection or App Server
launch. A no-grant case may start only from `IMPLEMENTING`. If a crash occurred
after `ACL_LOCKDOWN_INTENT` but before canonical grant issuance, the supervisor
accepts at most one exact case, grant, root, and principal-bound recovery
record, restores the snapshot, records `ACL_RESTORED`, and calls the canonical
`ccos-preissue-generation-abort-v1` transition. That transition preserves all
lifecycle counters and locks the case with
`PREISSUE_GENERATION_ABANDONED`. An orphaned `ISSUED` or `CLAIMED` grant is
rolled back to its sealed baseline, failed, and locked. A crash after canonical
completion but before journal completion or access-control restoration may
reconstruct only the missing `COMPLETED` record from the exact canonical result
and post-isolation digest, then restore. None of these paths creates another
implementation generation or grant.

After deterministic proposal, authentication, and schema preflight, the
supervisor records one `ccos-runtime-generation-attempt-v1` claim immediately
before the first App Server generation. Canonical grant issuance atomically
changes that claim to `GRANT_ISSUED`. Any ordinary pre-grant failure changes it
to `ABORTED` and locks the exact case in the same run. A process crash can leave
only `CLAIMED`, which the next startup aborts and locks before schema inspection
or App Server launch. No second App Server generation is permitted.

Worker authentication is configuration, not action authority. If
`auth.json` is absent at startup it must remain absent. If it already exists,
the supervisor records only its bounded file identity, size, link count, and
digest, never its content, and requires the same file and bytes to remain in
place. The supervisor never deletes pre-existing authentication material.

Publication is eligible only from `CLOSED_SUCCESS`. Merge, deployment, release,
credential changes, and universal synchronization are also ineligible before
`CLOSED_SUCCESS`. Only the publication child can present one of those external
actions to the guard. The guard first validates the role, exact associated
repository, at least one exact exclusive case binding, case state, and current
canonical head. Universal synchronization additionally requires the exact
bound universal bundle. Only a fully valid context receives
`SEPARATE_AUTHORITY_REQUIRED`. A role, repository, binding, state, or head
failure receives its specific denial instead, so a malformed request cannot be
misread as merely awaiting approval.

`SEPARATE_AUTHORITY_REQUIRED` is always a denial, never an authorization.
Merge, deployment, release, credential changes, and universal synchronization
remain outside the case lifecycle and require separate human or run-envelope
authority even after successful closure. The case store does not contain an
external-authority grant record, and successful case closure alone never
authorizes an external action.

## Canonical snapshot contract

The sole lifecycle snapshot contract is `ccos-git-snapshot-v1`. Candidate
freeze and repaired-candidate completion reject every other contract.

Every newly recorded lifecycle snapshot must retain the exact `contract`,
`sha256`, and full 40-character Git `head`. The stored `head` must equal the
candidate review or repaired head for that repository. Missing or mismatched
heads are rejected. A frozen legacy `review_snapshots` record with exactly the
older `contract` and `sha256` fields remains readable so an already active case
can proceed through authorized repair and closure, but it is never backfilled or
accepted for a new snapshot. Repaired snapshots always require `head`.

Run `snapshot --root <exact-repository-root> --head <full-commit-SHA>`. The
command requires a full 40-character commit hash and returns that exact `head`,
the `contract`, the SHA-256 digest, and the tracked file count. It performs the
following fail-closed checks:

1. Resolve `--root` and require it to be the exact root of a non-bare Git
   worktree.
2. Require the current Git `HEAD` to equal `--head` before enumeration, after
   object reads, and after the final cleanliness check.
3. Require a clean worktree before and after enumeration. A dirty tracked file,
   staged change, or nonignored untracked file fails the snapshot.
4. Enumerate the committed tree at `--head` with NUL-delimited Git output.
5. Accept only regular Git blobs with mode `100644` or `100755`. Reject symbolic
   links, submodules, unsupported modes or object types, malformed paths,
   malformed object identifiers, unsafe paths, and Unicode normalization
   collisions.
6. Read candidate bytes from immutable Git blob objects, never from mutable
   worktree files. The implementation does not follow filesystem links or
   resolve candidate paths outside the repository.
7. Normalize each repository-relative Git path to Unicode Normalization Form C,
   or NFC, and sort paths by their UTF-8 byte sequence.
8. Hash this byte stream with SHA-256:
   - literal bytes `CCOS-GIT-SNAPSHOT` followed by a zero byte
   - unsigned 64-bit big-endian length of the UTF-8 contract identifier
   - contract identifier bytes
   - unsigned 64-bit big-endian file count
   - for each sorted file, unsigned 64-bit path length and path bytes, unsigned
     64-bit mode length and mode bytes, then unsigned 64-bit content length and
     exact Git blob bytes

Ignored support data is not part of the committed Git tree. Changes under
ignored `.code-review-graph/`, cache folders, generated review metadata,
`node_modules/`, or the external case-state store therefore cannot change or
reopen a frozen candidate. A nonignored untracked file fails instead of being
silently omitted. Two clean worktrees at the same commit produce the same
digest, while a committed content or executable-mode change produces a
different digest.

The older explicit-entry and mutable-filesystem hashing helpers remain only for
compatibility and deterministic unit tests under `ccos-snapshot-v1`. That
legacy identifier is not accepted by lifecycle transitions and must not be
used for candidate freeze, repair completion, or closure evidence.

## Storage and locking

The store is `case-state.json` and uses schema version 2. Each mutation takes an exclusive standard
library lock. Windows uses `msvcrt.locking`; POSIX systems use `fcntl.flock`.
The engine validates schema and limits before use, writes a complete temporary
file, flushes it, and atomically replaces the store. Case and binding changes
therefore cannot diverge or leave a partially written state.

The clean Coding OS repository intentionally has no
`docs/delivery/current-state.md`. If a product-style session-start helper is
incorrectly applied to it, `start-helper-check` records
`start_helper_missing_current_state` as `CONTROL_FAILURE`. It must not create a
fake product manifest or classify the Coding OS product as defective.

## Erroneous terminal-closure quarantine

`quarantine-terminal` is the only recovery operation for a proven erroneous
`CLOSED_SUCCESS` record. It is not reopen, retry, repair, or publication
authority. The request must bind the exact case identifier, `CLOSED_SUCCESS`
state, revision, full case-record SHA-256, one-use request identifier, named
human authority, and fixed evidence reason. It creates a byte-exact protected
store backup, appends hash-chained `PREPARED` and `COMMITTED` audit records, and
transitions only that case to `CASE_LOCKED`. It preserves every lifecycle
counter and records whether external reconciliation is required. Idempotent
recovery may finish a prepared audit record, but the operation cannot reopen or
reset the case.

## CLI surface

Read-only commands are `store-status`, `show`, `list`, `resolve`,
`action-check`, and exact-head Git-object `snapshot`. `store-status` supplies
the exact store revision needed for safe registration. Branch `bind` and
`resolve` commands require `--repository`. `action-check` requires
`--actor-role` and accepts normalized repository, branch, worktree, pull
request, thread, universal bundle, head, and blocked-case context. Lifecycle
commands are `register`, `bind`, `start-implementation`,
`freeze-candidate`, `start-review`, `submit-review-completion`, `add-finding`, `freeze-findings`,
`close-without-blockers`, `authorize-repair`, `complete-repair`,
`observe-heads`, `start-closure-preflight`, `verify-closure-preflight`,
`complete-closure-check`, `control-failure`, `retry-control`, and
`start-helper-check`. Runtime-control commands bind controller-assigned actors,
issue one exact grant, claim, complete, or fail it. `record-hash` supplies the
exact case-record digest required by `quarantine-terminal`.

Pass `--json` for adapter output. Exit code `0` means success. Exit code `2`
means invalid input, a revision or binding conflict, a disallowed transition,
an exhausted limit, failed authority, or failed preflight. Python tracebacks and
other unexpected runtime failures retain the normal nonzero interpreter exit.
