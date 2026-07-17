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

The single atomic store contains both cases and the binding registry. The
registry can bind normalized repository URLs, branches, worktrees, pull
requests, thread or task identifiers, and universal bundle identifiers. An
identifier already owned by one case cannot be rebound to another case.

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
finding set once. Later findings remain visible but are marked late and
non-authorizing.

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

`action-check` evaluates one target case. A lock on one case does not block a
different case or unrelated product work. A global emergency stop is outside
this case engine and is reserved for credential compromise or uncontrolled
concurrent mutation.

Publication is eligible only from `CLOSED_SUCCESS`. Merge, deployment, release,
credential changes, and universal synchronization always require separate
authority even after successful closure.

## Canonical snapshot contract

The snapshot contract identifier is `ccos-snapshot-v1`.

1. Enumerate regular files under the repository root.
2. Exclude every path under `.git` and the explicit case-state data root.
3. Reject symbolic links.
4. Convert each repository-relative path to POSIX separators and normalize it
   to Unicode Normalization Form C, or NFC.
5. Reject unsafe paths and collisions created by normalization.
6. Sort paths by their UTF-8 byte sequence, not by shell or locale ordering.
7. Preserve file bytes exactly.
8. Hash this byte stream with SHA-256:
   - literal bytes `CCOS-CASE-SNAPSHOT` followed by a zero byte
   - unsigned 64-bit big-endian length of the UTF-8 contract identifier
   - contract identifier bytes
   - unsigned 64-bit big-endian file count
   - for each sorted file, unsigned 64-bit path length, path bytes, unsigned
     64-bit file length, then file bytes

The same entries therefore produce the same digest regardless of PowerShell,
Python filesystem enumeration order, locale, or operating system.

## Storage and locking

The store is `case-state.json`. Each mutation takes an exclusive standard
library lock. Windows uses `msvcrt.locking`; POSIX systems use `fcntl.flock`.
The engine validates schema and limits before use, writes a complete temporary
file, flushes it, and atomically replaces the store. Case and binding changes
therefore cannot diverge or leave a partially written state.

The clean Coding OS repository intentionally has no
`docs/delivery/current-state.md`. If a product-style session-start helper is
incorrectly applied to it, `start-helper-check` records
`start_helper_missing_current_state` as `CONTROL_FAILURE`. It must not create a
fake product manifest or classify the Coding OS product as defective.

## CLI surface

Read-only commands are `store-status`, `show`, `list`, `resolve`,
`action-check`, and `snapshot`. `store-status` supplies the exact store revision
needed for safe registration. Lifecycle commands are `register`, `bind`, `start-implementation`,
`freeze-candidate`, `start-review`, `add-finding`, `freeze-findings`,
`close-without-blockers`, `authorize-repair`, `complete-repair`,
`observe-heads`, `start-closure-preflight`, `verify-closure-preflight`,
`complete-closure-check`, `control-failure`, `retry-control`, and
`start-helper-check`.

Pass `--json` for adapter output. Exit code `0` means success. Exit code `2`
means invalid input, a revision or binding conflict, a disallowed transition,
an exhausted limit, failed authority, or failed preflight. Python tracebacks and
other unexpected runtime failures retain the normal nonzero interpreter exit.
