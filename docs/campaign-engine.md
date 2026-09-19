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

`prepare` derives repository identities, the installed runtime pin, command
working directories, and source hashes from a proposed change. It writes a new
specification file without creating or starting a campaign. Business scope,
spending, deadlines, operation limits, and external-action authority remain
explicit approved inputs. Routine engineering values are prepared by the agent.

Each prepared node has `acceptance_scenarios`. A scenario names its existing
requirement, expected behavior, validation command, and source references.
Each reference binds a repository path and requirement identifier to SHA-256
content. An optional `section` selects one exact Markdown heading and its
subsections. Duplicate headings are rejected. Use the existing requirement
document, relevant dependency lockfile, and independent acceptance test where
applicable. An unrelated section does not invalidate this dependency.

Source references are checked during preparation, admission, approval,
validation, review, and delivery. Changed or missing relevant sources stop
affected acceptance. Passing validation evidence also binds the actual command,
executable, boundary launcher, environment, and revision. Changed inputs require
fresh evidence. Source links do not prove that an assertion captures customer
intent. Independent review still checks the requested behavior.

`required_tools` names the actual tools a node needs. Admission checks these
against the existing mediated host surface. Browser execution is currently
outside that surface and is rejected when required. Missing unrelated plugins
do not invalidate a supported engine task.

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

Campaign validation uses a read-only process boundary. Windows uses the native
Codex `:read-only` profile, Linux uses Bubblewrap, and macOS uses `sandbox-exec`.
Windows requires an initialized `elevated` backend. In PowerShell opened as
Administrator for the account that will run campaigns, provision it once with
the [official Codex setup command](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/cli/src/sandbox_setup.rs):

```powershell
codex sandbox setup --elevated --current-user --codex-home "$env:USERPROFILE\.codex"
if ($LASTEXITCODE -ne 0) { throw 'Windows validation sandbox provisioning failed.' }
```

For Codex 0.154.0, finish directory-read setup with the public
`scripts/prepare_windows_validation_boundary.py` command in
[Getting Started](getting-started.md#install-an-exact-source-commit).
The native setup request delegates read grants to a background helper. This
bounded completion check binds one fresh setup request to its exact directory,
native source hashes, and appended log generation before validation begins.
It preserves a receipt on success or failure, runs no model turn or project
command, and never retries. Existing account provisioning is required.

Before the first campaign, run the paired product-file read and denied-write
test documented in [Getting Started](getting-started.md#verify-the-installed-runtime)
from the verified package source. `doctor` verifies the runtime and store and
can probe the native host, but it does not verify validation file access.
Windows validation preserves the host's `USERPROFILE`, `TEMP`, and `TMP`
directories for native sandbox bootstrap. Commands cannot override those values.
The existing environment digest binds them, so changing a host temporary
directory requires fresh validation evidence.

The required launcher must exist and start successfully. There is no unconfined
fallback. Commands that need to write build outputs or caches are outside this
validation mode. The standalone trusted-command utility still supports ordinary
process execution, which does not carry the campaign containment guarantee.
Linux hosts must permit Bubblewrap's unprivileged user namespaces. Ubuntu hosts
may require the executable-specific AppArmor prerequisite and launch check in
[Getting Started](getting-started.md). A missing host prerequisite stops validation.

A correction policy can recognize one failed Python `unittest` assertion tied
to an approved acceptance scenario. The persisted command receipt, clean exact
revision, unchanged authority, unused correction, and remaining deadline must
all match. The existing budgets must cover the correction, complete validation,
required review cohort, and remaining delivery effects before it can start.
Unknown exits, test errors, invalid evidence, exhausted limits, and cancellation
do not qualify. Earlier operation expenditure is retained.

Blocking findings need an acceptance expectation, an established invariant with
evidence, or a reproducible regression. Explicit nonblocking observations remain
in the review evidence without opening another repair cycle. Repeated stable
finding identifiers with identical evidence collapse into one finding even when
their titles differ. Conflicting evidence under one identifier is rejected.
Different identifiers are not merged through text similarity.

Native workers receive the approved node, its relevant sources, and the scoped
campaign tools. Automatic skill-catalogue instructions are disabled for those
worker sessions so they do not repeat the parent's capability selection.
Project instructions and the existing execution boundary still apply. Review
responses use `PASS` or `BLOCK`; unsupported verdicts remain rejected.

Recovery consumes already attested completed worker results and reconciles
uncertain effects through their existing identifiers. It does not restart an
uncertain writer. Cancellation, attempts, and expenditure survive restart.
If the supervisor is recreated while the original native host connection
survives, it can collect the same read-only review turn. Recovery verifies the
process creation identity, live connection, frozen candidate, source bindings,
authority and complete active review cohort. It starts no new turn and spends
no new attempt. Losing that connection or any required cohort member fences the
affected work. This branch does not reconnect a lost native process or replace
a writer.

## Evidence

Objective completion is reported from exact product evidence. Engine state and
process status are reported separately. A passing shape validator, comment,
handoff, branch, pull request, or local state mirror is not objective evidence
by itself.

Status is rendered from the external store and writes no repository status
document. Manual pull requests identify the actual manual request, scope,
revisions, validation, and review. Campaign pull requests bind their metadata to
the admitted campaign and current candidate. Changing the body label cannot
remove that requirement. Merge checks the current remote body again.
The specification supplies narrative-only pull request text. The supervisor
appends the current specification digest, candidate, actual validation and review
evidence, and operation identifiers when preparing the durable effect. Reserved
metadata in the narrative is rejected. Checks outside the approved node are
reported as `NOT_CONFIGURED`, never as passing tests.

Operation budgets count operations, not model tokens. Native usage notifications
are recorded at the host boundary in the existing telemetry table. Cumulative
reports are deduplicated per bound actor thread, including failed and cancelled
work. Input plus output gives total model tokens. Cached input and reasoning
output remain subsets, not extra additions. Missing or inconsistent reports are
unavailable. Unbound parent activity is explicitly outside measured coverage.
This is retrospective metering, not an exact spend cap.

For comparisons, divide tokens across all attempts by independently accepted
comparable changes. Divide founder minutes across all attempts by the same
denominator. Both ratios are undefined when no change is accepted. Subscription
charges and any future usage-price estimate remain separate.

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
