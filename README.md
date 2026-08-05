# Codex Coding OS

Codex Coding OS is a deterministic campaign engine for bounded AI-assisted
software delivery. One immutable campaign specification binds the repository,
worktree, branch, base commit, allowed paths, finite dependency graph,
validation commands, reviewers, budgets, stop conditions, runtime pin, and
publication authority.

The campaign reducer and the external SQLite store are the only lifecycle
authority. Repository state files, handoffs, comments, branch names, and
caller-declared roles do not authorize or block work.

## Engine

The implementation lives in `scripts/agent/campaign_engine/`:

| Component | Responsibility |
|---|---|
| `model.py` | Immutable campaign contracts and typed state |
| `reducer.py` | The single pure lifecycle reducer |
| `store.py` | Durable SQLite state, revisions, fencing, leases, outbox, and evidence |
| `admission.py` | Exact repository, worktree, remote, base, scope, and installed-runtime checks |
| `supervisor.py` | Deterministic graph selection, worker dispatch, validation, waiting, recovery, and cancellation |
| `host.py` | Native idle-task creation, bind-before-turn, write boundaries, interruption, and receipts |
| `evidence.py` | Trusted command execution and exact-head evidence |
| `effects.py` | Idempotent push, pull request, comment, merge, and exact-file effects |
| `legacy.py` | Read-only inspection and evidence-preserving legacy archival |
| `cli.py` | Public executable interface |

The formal model is in `formal/Campaign.tla`. The executable reducer remains
the implementation authority.

## State and runtime identity

Volatile execution state is stored only at:

```text
%USERPROFILE%\.codex\coding-os-state\campaigns.sqlite3
```

The installed runtime is pinned by all six fields below:

- source commit
- bundle digest
- install transaction
- protocol version
- schema compatibility
- host capability probe version

Admission, approval, lifecycle resumption, action authorization, reconciliation,
and `doctor` reject an incomplete or mismatched pin. Cancellation remains
available so STOP cannot be blocked by a damaged runtime.

## Install

From an exact clean source commit:

```powershell
$SourceCommit = git rev-parse HEAD
.\scripts\package.ps1
$BundleDigest = (Get-Content .\install-bundle.manifest.json | ConvertFrom-Json).aggregate_sha256
.\scripts\install.ps1 -ExpectedSourceCommit $SourceCommit -ExpectedBundleSha256 $BundleDigest
```

The transactional installer verifies the bundle, promotes the support tree and
managed skills, installs the campaign hook, initializes the SQLite store, and
records the runtime pin. `CodexHome` must be the operating-system account
profile's `.codex` directory, `%USERPROFILE%\.codex` on Windows. The public
installers reject a command-line or environment override that resolves
elsewhere because the runtime bootstrap uses that canonical account-profile
path. `SkillsRoot` must be its `skills` directory, and the public installers
also reject any other skills root. A clean first install and v3 reinstall or
uninstall use this canonical nested layout without a
migration flag. Only an existing strict v2 install at that layout requires
`-LegacyOverlapMigration`, which preserves its migration evidence. Other
overlapping root layouts are rejected. Use the installer help for explicit
source and legacy archive options:

```powershell
.\scripts\install.ps1 -Help
```

Linux and macOS use:

```bash
source_commit="$(git rev-parse HEAD)"
./scripts/package.sh
bundle_digest="$(python -c 'import json; print(json.load(open("install-bundle.manifest.json"))["aggregate_sha256"])')"
./scripts/install.sh --expected-source-commit "$source_commit" --expected-bundle-sha256 "$bundle_digest"
```

Universal policy installation is an explicit installer action. It requires
`-InstallUniversalPolicy` with a policy authority source and reference. A
campaign publication authority additionally binds the campaign ID, node ID,
authority epoch, cancellation epoch, exact candidate source commit, and the
`EXACT_FILE_REPLACE` effect. Legacy archival is separately opt-in and keeps the
source bytes unchanged.

## Public commands

Use the installed executable:

```powershell
$Engine = "$env:USERPROFILE\.codex\coding-os\scripts\agent\campaign_engine\cli.py"
python $Engine --json doctor
python $Engine --json admit --spec .\campaign.json
python $Engine --json approve --campaign-id <id> --specification-digest <digest>
python $Engine --json run --campaign-id <id>
python $Engine --json status --campaign-id <id>
python $Engine --json cancel --campaign-id <id>
python $Engine --json reconcile --operation-id <operation-id>
python $Engine --json legacy inspect --source "$env:USERPROFILE\.codex\case-state"
```

`run` advances until a named external event or terminal result. It does not
poll indefinitely. Every autonomous operation spends its durable budget token
before execution.

## Worker authority

Automated workers are created as idle native Codex tasks. The engine verifies
the returned native task identity, worktree, and sandbox, commits that exact
binding to SQLite, and only then starts the first turn.

- Implementers and the one combined repair receive only approved writable paths.
- Parents, reviewers, and closure reviewers receive read-only sandboxes.
- One-use leases bind the actor, native task, authority epoch, cancellation
  epoch, fencing epoch, repository, worktree, candidate head, and scope.
- Late or stale results are rejected.

Caller-declared roles, prompt text, process names, and lease-shaped strings are
not authority by themselves.

## Validation and review

The trusted runner executes admitted commands without a shell and binds each
result to the exact executable, argument array, working directory, environment
allowlist, timeout, output limit, candidate head, worktree condition, and
required exit code. A nonzero process exit is failure even when output contains
passing assertions.

The review cohort evaluates one frozen exact-head diff. The finding set freezes
once. A campaign can use one combined repair, one complete revalidation, and
one closure review. Remaining or repair-introduced findings fail that exact
node. The engine cannot create another repair, review generation, or successor
campaign.

## External effects

Push, pull request, comment, and merge operations use stable operation IDs and
the durable outbox:

```text
PREPARED -> EXECUTING -> CONFIRMED | FAILED | AMBIGUOUS | CANCELLED
```

An ambiguous mutation is reconciled by querying the external system. It is
never blindly repeated. Publication stays bound to the frozen candidate head
and the immutable required-effect sequence.

## STOP

`cancel` durably increments the cancellation epoch, cancels queued nodes,
invalidates leases, interrupts owned workers and process trees, prevents new
effects, rejects late results, reconciles uncertain effects, and ends the
campaign in `CANCELLED`. Restart recovery never resumes a cancelled campaign.

## Legacy retirement

`scripts/agent/case_state.py` is a permanent command stub. Every former mutation
returns `LEGACY_ENGINE_RETIRED` with exit code 78. The new engine never imports
or calls the retired engine.

Legacy state is copied into a verified read-only archive. Unresolved cases are
classified `LEGACY_ARCHIVED_UNRESOLVED`. Historical records are never activated
inside the new store and never translated into a new outcome.

## Repository adapters

Product repositories use thin clients that call the installed engine. Hosted
checks never read live local SQLite state. The exact CI signals are:

- `product-quality`
- `product-tests`
- `product-acceptance`
- `requested-documentation`
- `coding-os-adapter`
- `pr-metadata`

Only explicitly requested documentation is checked by
`requested-documentation`. Adapter failures report adapter status and do not
claim that product behavior is defective.

## Development validation

Run the focused engine suite first:

```powershell
python -B -m unittest tests.test_campaign_model_reducer -v
python -B -m unittest tests.test_campaign_store -v
python -B -m unittest tests.test_campaign_runtime_components -v
```

Then run the repository pack validation and installer tests:

```powershell
.\scripts\validate-pack.ps1
python -B .\tests\test_install_transaction.py
.\tests\install-uninstall-smoke.ps1
```

The incident corpus is indexed at `tests/fixtures/incidents/index.json`. Every
indexed incident has one historical-failure fixture and one opposite-case
fixture.

## Source map

- [Campaign engine contract](docs/campaign-engine.md)
- [Getting started](docs/getting-started.md)
- [Review doctrine](docs/review-doctrine.md)
- [System scope](docs/system-scope.md)
- [Full skill inventory](docs/full-skill-inventory.md)
- [Publishing checklist](docs/publishing-checklist.md)
- [Changelog](CHANGELOG.md)

## Current source status

Mutable publication facts are read from their live authorities:

- [GitHub Releases](https://github.com/AymanShams/codex-coding-os/releases)
- [GitHub pull requests](https://github.com/AymanShams/codex-coding-os/pulls)
- [GitHub Actions](https://github.com/AymanShams/codex-coding-os/actions)
- [`pack.manifest.json`](pack.manifest.json) for package membership and version
- [`install-bundle.manifest.json`](install-bundle.manifest.json) for the exact built bundle

## Package inventory

Inventory is derived from source instead of copied into prose. Run
`git ls-files` for tracked paths, inspect `pack.manifest.json` for declared
package members, and inspect `install-bundle.manifest.json` for the exact bundle
entries and aggregate digest produced by packaging.

## License

See `LICENSE.md`, `COMMERCIAL-USE.md`, `NOTICE.md`, and
`THIRD_PARTY_SKILLS.md`.
