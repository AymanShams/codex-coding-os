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

`start-review` declares one immutable `ccos-review-cohort-v2` cohort before any
finding is accepted. Each required reviewer assignment binds a reviewer
identifier, controller-observed native child and parent thread identifiers,
canonical agent path, repository, exact reviewed head, snapshot, exact scope
string, and the SHA-256 digest of that UTF-8 string. Every native reviewer must
be one direct child of the same native parent. The cohort cannot grow, shrink,
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
finding set once, but only after every required reviewer has one verified
`ccos-review-completion-v2` receipt. The mutation accepts only the frozen
reviewer identifier. It does not accept a caller-supplied receipt, thread,
completion state, finding set, timestamp, or evidence digest. The verifier
resolves the canonical Codex sessions root from the case-state root, proves one
direct native child identity from `session_meta`, and derives the receipt only
from one ordered `task_started` and `task_complete` turn whose raw final message
is an exact v2 completion payload. It binds the log prefix, final message,
timestamps, cohort declaration time, assignment, and evidence digests. The
rollout and every ancestor through the canonical Codex profile are checked
before and after the read for direct-path identity, stable bytes, consistent
owner, and an exact read-only sandbox access entry. This verifier fails closed
outside Windows until equivalent operating-system ownership and access-control
proof exists.

Persisted v1 cohorts and receipts are read without backfill. They remain
unverified and cannot freeze findings, enter closure, or authorize publication.
`attest-existing-review-completion --reviewer-id` is the only migration path.
It anchors the original v1 completed-turn identifier in the same native rollout
and requires a later raw v2 restatement from that reviewer. A successful
attestation adds a separately hashed `native_verification` record while
preserving the original v1 cohort bytes, receipt fields, and receipt digest.
`FAILED` or `INCOMPLETE` is a recorded terminal cohort result and cannot be
treated as completion. Missing, unverified, or non-completed receipts block the
relevant transition without changing the revision. Later findings remain
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

## Artifact-authorized runtime action boundary

New production action authorization uses
`ccos-proposal-action-grant-v2`. V2 is an actorless, one-use capability stored
in the existing `runtime.action_grants` map. App Server identities, parent or
child roles, thread identifiers, turn identifiers, task paths, approval
results, and proposal-producing process identity are not fields in its
authorization decision.

The only supported operation is `replace_existing_file_v1`: replacement of one
already existing tracked file with the exact bytes of one bound proposal
artifact. The operation does not authorize a command, patch interpreter, path
selection, content supplied at execution time, create, delete, rename,
multi-file change, commit, push, pull request, publication, deployment,
credential change, or universal synchronization.

### V2 grant contract

Before `ISSUED`, the engine binds all of the following in one canonical record:

- protocol `ccos-proposal-action-grant-v2`
- stable grant identifier and exact case identifier
- exact expected case revision
- exact normalized repository, branch, and worktree
- exact full base-head commit SHA
- exact normalized existing target path and baseline SHA-256 digest
- exact normalized proposal artifact path, SHA-256 digest, and byte size
- exact replacement SHA-256 digest for the proposal bytes
- fixed operation `replace_existing_file_v1`
- exact broker security identifier, or SID
- exact `broker_dacl_v1` evidence mode
- absolute expiry
- pinned source and protocol identifiers required by the installed bundle

The expected revision is a mutation interlock. Issuance fails without changing
state when it is stale, when a grant already exists, or when any required
binding is absent or malformed. V1 and v2 share the one-grant-per-case limit.
Issuance cannot reset, replace, or append to an existing grant.

The proposal artifact remains untrusted until the action boundary. The broker
rejects symbolic links, reparse points, hard-linked aliases, unexpected file
identity, or an unexpected byte count. It reads the exact declared number of
bytes and requires their digest to equal both the proposal digest and the
replacement digest in the grant. Those bytes are the complete replacement
payload. The caller cannot substitute a target, alternate artifact, command,
role, thread, or content.

### Claim and execution

Before issuance, the grant is durably `ARMED` while the protected roots are
narrowed. A supervisor restart at `ARMED` cancels that exact arm, restores or
proves the journal-bound original access controls, and locks the case. A
durable `CANCELLED` record is reverified the same way and cannot restart the
controller. The executable lifecycle is `ARMED` to `ISSUED` to `CLAIMED` to
`COMPLETED` or `FAILED`, with `ARMED` to `CANCELLED` as the only pre-issuance
terminal path.
Only the first successful, non-idempotent claim creates write authority. An
idempotent response may report the prior claim but cannot authorize another
write. An expired grant, stale revision, or failed preclaim check terminally
consumes the grant as `FAILED` and locks the exact case.

The fixed separate-process broker is the only process permitted to exercise
the capability. At the actual replacement boundary it independently verifies:

1. Its operating-system SID equals the exact broker SID in the grant.
2. The case, expected revision, grant identifier, protocol, operation, status,
   expiry, and source pins are exact.
3. Repository, branch, worktree, and full base head are exact and the worktree
   has the required clean baseline.
4. Target path, stable identity, link count, and baseline digest are exact.
5. Proposal path, stable identity, link count, size, proposal digest, and
   replacement digest are exact.
6. Broker-owned protected-root access controls contain explicit recursive
   mutation denials for the proposal generator, Offline sandbox account, and
   sandbox group. V2 verifies that boundary directly and never launches a
   nested Codex sandbox as part of authorization.

The broker then claims the grant, atomically replaces only the bound target,
verifies the exact replacement digest and changed-path set, records canonical
completion, and denies replay. Parent, implementation, reviewer, closure,
incomplete, unknown, or forged agent identities have no role-specific mutation
path because no agent identity is action authority.

### Journal, restart, and failure

The protected `broker-journal` is hash chained and single-instance locked. It
records preclaim, claim, replacement, completion, failure, broker SID and
process identifier, grant and proposal hashes, claim and result hashes, exact
target, pre-action and post-action digests, and exact changed paths.

Recovery from `CLAIMED` is bounded by that journal. When the target is still the
exact baseline, the recovery path may perform the one declared replacement.
When the exact replacement is already present, it may complete without another
write. Any third state rolls back atomically to the sealed baseline, records
`FAILED`, and locks the exact case. Recovery never returns a grant to `ISSUED`,
changes its bindings, or permits a second replacement.

Any repository, branch, worktree, head, target, baseline, proposal,
replacement, SID, expiry, revision, access-control, journal, or source-pin
mismatch fails closed. A failed claim or pre-action verification consumes the
grant. A post-claim failure invokes rollback and case locking. Completion
requires exactly one changed path and the exact replacement digest.

### V1 compatibility boundary

Existing `ccos-runtime-action-grant-v1` records remain readable for historical
inspection and bounded terminal recovery. An already issued or claimed v1
grant may reach `COMPLETED` or `FAILED` only under its original receipt, actor,
HMAC, journal, rollback, and lock rules. It cannot be released, reset,
converted, cloned, or reissued.

New v1 issuance is disabled. All new production action grants use
`ccos-proposal-action-grant-v2`. V1 fields must not be inferred for v2, and v2
must not acquire role, App Server, thread, turn, approval, controller-receipt,
or supervisor-HMAC dependencies.

ADR 0002 is the production authorization decision. ADR 0001 remains the
historical v1 design and still controls recovery interpretation for records
that already exist.

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
`freeze-candidate`, `start-review`, `submit-review-completion`,
`attest-existing-review-completion`, `add-finding`, `freeze-findings`,
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
