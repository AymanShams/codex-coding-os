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

For the complete installation, including managed universal policy, use an exact
clean tagged Git checkout:

```powershell
$SourceCommit = (git rev-parse HEAD).Trim()
$BundleDigest = (Get-Content .\install-bundle.manifest.json | ConvertFrom-Json).aggregate_sha256
.\scripts\install.ps1 `
  -ExpectedSourceCommit $SourceCommit `
  -ExpectedBundleSha256 $BundleDigest `
  -InstallUniversalPolicy `
  -PolicyAuthoritySource explicit-user-approval `
  -PolicyAuthorityReference "approved-tagged-installation"
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
overlapping root layouts are rejected. Use PowerShell help for the complete
source and legacy archive options:

```powershell
Get-Help .\scripts\install.ps1 -Full
```

The published ZIP contains no `.git` directory. Verify its SHA-256 sidecar and
use `-ArchiveMode` with the exact 40-character release commit recorded in the
release notes. Archive mode installs the engine but preserves universal policy
and cannot install or remove it. Use the tagged Git checkout when universal
policy must be changed. [Getting Started](docs/getting-started.md) contains both
command paths.

Linux and macOS use:

```bash
source_commit="$(git rev-parse HEAD)"
bundle_digest="$(python3 -c 'import json; print(json.load(open("install-bundle.manifest.json"))["aggregate_sha256"])')"
./scripts/install.sh \
  --expected-source-commit "$source_commit" \
  --expected-bundle-sha256 "$bundle_digest" \
  --install-universal-policy \
  --policy-authority-source explicit-user-approval \
  --policy-authority-reference approved-tagged-installation
```

The release bundle manifest is committed with the exact source. Packaging a
new release is a maintainer operation performed with `scripts/package.ps1` on
a clean reviewed commit.

Universal policy handling is tri-state. Omitting both policy action flags
preserves a previously managed global `AGENTS.md` and `default.rules` unchanged.
Explicit removal uses `-RemoveUniversalPolicy` on PowerShell or
`--remove-universal-policy` on Linux and macOS. Explicit installation uses
`-InstallUniversalPolicy` or `--install-universal-policy` together with a policy
authority source and reference. A campaign publication authority additionally
binds the campaign ID, node ID, authority epoch, cancellation epoch, exact
candidate source commit, and the `EXACT_FILE_REPLACE` effect. Legacy archival is
separately opt-in and keeps the source bytes unchanged.

## Public commands

Use the installed executable:

```powershell
$Engine = "$env:USERPROFILE\.codex\coding-os\scripts\agent\campaign_engine\cli.py"
python -B $Engine --json doctor
python -B $Engine --json admit --spec .\campaign.json
python -B $Engine --json approve --campaign-id <id> --specification-digest <digest>
python -B $Engine --json run --campaign-id <id>
python -B $Engine --json status --campaign-id <id>
python -B $Engine --json cancel --campaign-id <id>
python -B $Engine --json reconcile --operation-id <operation-id>
python -B $Engine --json legacy inspect --source "$env:USERPROFILE\.codex\case-state"
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

## Capability routing and security

The canonical router candidate under `capability-routing/` is dormant repository
source. The installer does not register it as a hook, build a live capability
manifest, create a route registry, or change universal Codex routing state.

Codex Security supplies 13 plugin-managed security workflows. Supabase and Neon
Postgres supply their own provider skills and connectors. These third-party
capabilities stay managed by Codex and are never copied into this repository.
The repository bundles provider-neutral security guidance, including PostgreSQL
roles, grants, row-level security, views, privileged functions, and regression
tests.

Secure-by-default guidance supports implementation that changes an actual auth,
permission, secret, public-endpoint, database-access, or frontend security
boundary. A Codex Security scan is selected only from explicit scan or finding
intent and the real review surface.

See [Security Capability Operating Model](docs/security-capability-operating-model.md)
for the complete skill map, selection rules, provider composition, fallback
limits, and retired-router migration.

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

## Legacy engine retirement

`scripts/agent/case_state.py` is a permanent command stub. Every former mutation
returns `LEGACY_ENGINE_RETIRED` with exit code 78. The new engine never imports
or calls the retired engine.

Legacy state is copied into a verified read-only archive. Unresolved cases are
classified `LEGACY_ARCHIVED_UNRESOLVED`. Historical records are never activated
inside the new store and never translated into a new outcome.

Version 1.0 retired the former case registration and transition commands,
controller and broker lifecycle authority, actor-binding authority, review and
repair generation, session-state gates, current-state equality, active-slice
permission, handoff authority, path-only classification, anti-loop lifecycle
authority, and direct installed mutation rules.

Those paths were retired because they could form several competing authorities.
Stale repository or session metadata could disagree with the real Git head,
worker identity, review evidence, or external effect and then falsely stop work,
falsely permit work, or create another support cycle. The replacement puts every
lifecycle transition in one pure reducer, every volatile decision in one
external SQLite store, and every publication mutation behind one durable,
reconcilable outbox.

The denial stub is not a compatibility engine or fallback. What remains is
deliberately narrow: read-only legacy evidence, stable product specifications,
and exact-file replacement verification owned by the new engine. See
[Legacy Case Engine Retirement](docs/case-state-contract.md) for the complete
boundary and [Getting Started](docs/getting-started.md) for the 0.x upgrade.

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
- [Legacy case engine retirement](docs/case-state-contract.md)
- [Getting started](docs/getting-started.md)
- [Review doctrine](docs/review-doctrine.md)
- [Security capability operating model](docs/security-capability-operating-model.md)
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
