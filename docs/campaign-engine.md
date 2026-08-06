# Campaign Engine Contract

The campaign engine is the sole executable lifecycle authority in Codex Coding
OS. Its immutable specification, pure reducer, and durable SQLite store define
what can happen. Rules, skills, hooks, adapters, repository files, comments,
and prompts are clients or evidence sources only.

## Immutable specification

Approval binds the campaign identifier and specification digest to the exact
objective, objective kind, mode, authority and cancellation epochs, repository
remote, Git root, worktree, branch, base commit, allowed paths, finite directed
acyclic graph, validation commands, review cohort, publication sequence,
attempt budgets, deadlines, stop conditions, installed source commit, bundle
digest, install transaction, protocol version, schema compatibility, and host
capability probe version.

The graph and specification cannot change after approval. A material change
requires a new user-approved specification revision. The engine cannot create
that revision itself.

## Lifecycle implementation

`scripts/agent/campaign_engine/reducer.py` contains the only lifecycle reducer:

```python
reduce(snapshot, event) -> next_snapshot, effect_intents
```

It has no filesystem, process, Git, network, model, clock, or identifier access.
Every transition checks the exact store revision, authority epoch, cancellation
epoch, and applicable fencing epoch. The executable transition relation is
tested exhaustively and mirrored in `formal/Campaign.tla`.

## Durable store

`CampaignStore` uses SQLite with foreign keys, write-ahead logging, full
synchronous durability, immediate write transactions, compare-and-swap
revisions, monotonic fences, unique request and operation identities, migration
backups, startup integrity checks, and recovery of interrupted effects.

Each approval, resumed run, protected actor action, and effect reconciliation
first re-verifies the six-field installed runtime pin bound into the campaign.
Cancellation remains available when runtime verification fails.

The database contains campaign and node snapshots, dependencies, actors,
leases, operations, effects, evidence, reviews, findings, resource locks,
events, runtime installations, telemetry, and legacy archive records.

## Authority boundaries

- The supervisor chooses only from the approved finite graph.
- Native workers are bound while idle before their first turn.
- Write-capable workers receive only the exact approved scope.
- Parent and reviewer workers are read-only.
- Validation commands are executed only by the trusted runner.
- Publication requires the recorded authority, next required effect, and frozen
  candidate head.
- Ambiguous external effects are queried before any further decision.
- STOP invalidates every stale execution identity.

## Evidence

Objective completion is reported from exact product evidence. Engine state and
process status are reported separately. A passing shape validator, comment,
handoff, branch, pull request, or local state mirror is not objective evidence
by itself.

## Legacy boundary

The former case engine is not a fallback and has no mutation surface. Its public
commands return `LEGACY_ENGINE_RETIRED`. `legacy.py` can only inspect and archive
old records as read-only evidence.

The replacement removed competing lifecycle authorities because Git state,
session metadata, handoffs, caller roles, hooks, and runtime commands could
disagree or become stale. The single reducer and external store prevent those
sources from independently stopping, approving, reviewing, repairing, or
publishing work. See [Legacy Case Engine Retirement](case-state-contract.md) for
the retired surfaces and preserved evidence boundary.
