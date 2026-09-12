# Getting Started

## Install an exact source commit

The package requires Python 3.11 or newer and Git. Automated native workers
also require a compatible, authenticated Codex CLI. Campaign validation needs
Codex's read-only sandbox on Windows, Bubblewrap on Linux, or `sandbox-exec`
on macOS. GitHub delivery additionally needs authenticated `gh`.

Windows requires an initialized native `elevated` Codex sandbox. With Codex CLI
0.154.0 or a compatible version installed, open PowerShell as Administrator and
run its setup once for the account that will run campaigns:

```powershell
codex sandbox setup --elevated --current-user --codex-home "$env:USERPROFILE\.codex"
if ($LASTEXITCODE -ne 0) { throw 'Windows validation sandbox provisioning failed.' }
```

The [native Windows sandbox documentation](https://learn.chatgpt.com/docs/windows/windows-sandbox)
describes the backend. The [official setup command](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/cli/src/sandbox_setup.rs)
provisions it and saves the configuration. Having `codex` on `PATH` alone does
not establish product-file read access.

Linux must also permit Bubblewrap to create unprivileged user namespaces.
On Ubuntu 24.04 and later, an administrator may need to grant `userns` to the
installed Bubblewrap executable through an application-specific AppArmor
profile, as described in the [Ubuntu release notes](https://documentation.ubuntu.com/release-notes/24.04/).
Verify the prerequisite before starting a campaign:

```bash
bwrap --ro-bind / / --proc /proc --dev /dev --unshare-pid \
  --die-with-parent --chdir "$PWD" -- /usr/bin/true
```

The command must exit successfully. Keep the read-only mounts and the host's
system-wide namespace restrictions in place.

The routed skill entry requires a separately installed canonical universal
router. The package does not install or activate that router. `doctor` reports
its pointer, manifest hash, and CLI availability separately from engine
integrity. That prerequisite check is not a route-admission receipt. The
canonical router must still admit the actual task. Installing a native plugin
does not install the engine or the universal router.

Use a clean checkout at the tag or commit you intend to install. A tagged Git
checkout is the complete installation path because it can also install the
managed universal policy:

```powershell
git status -sb
.\scripts\validate-pack.ps1
$SourceCommit = (git rev-parse HEAD).Trim()
$BundleDigest = (Get-Content .\install-bundle.manifest.json | ConvertFrom-Json).aggregate_sha256
.\scripts\install.ps1 `
  -ExpectedSourceCommit $SourceCommit `
  -ExpectedBundleSha256 $BundleDigest `
  -InstallUniversalPolicy `
  -PolicyAuthoritySource explicit-user-approval `
  -PolicyAuthorityReference "approved-tagged-installation"
```

The install transaction verifies the bundle, atomically promotes managed files,
installs the campaign hook, initializes the external store, and records the six
runtime-pin fields.

`CodexHome` must be the operating-system account profile's `.codex` directory,
`%USERPROFILE%\.codex` on Windows. The public installers reject a command-line
or environment override that resolves elsewhere because the runtime bootstrap
uses that canonical account-profile path. `SkillsRoot` must be
`%USERPROFILE%\.codex\skills`, and any other skills root is rejected. Clean
first installs and v3 reinstalls or uninstalls need no overlap or migration
option. Pass `-LegacyOverlapMigration` only when upgrading an existing strict
v2 install in that exact nested layout. All other overlapping root layouts
remain invalid.

The complete `runtime_pin` records source commit, bundle digest, install
transaction, protocol version, schema compatibility, and host capability probe
version. The source runtime record and installed bundle must agree with every
field.

Use `-ArchiveLegacyState` only when the unchanged legacy source at
`$env:USERPROFILE\.codex\case-state` should be ingested as read-only evidence.
Universal policy handling is tri-state. Omitting both policy action flags
preserves a previously managed global `AGENTS.md` and `default.rules` unchanged.
Explicit removal uses `-RemoveUniversalPolicy` on PowerShell or
`--remove-universal-policy` on Linux and macOS. Explicit installation uses
`-InstallUniversalPolicy` or `--install-universal-policy` together with an
explicit policy authority source and reference. Campaign publication authority
also requires the exact campaign ID, node ID, authority epoch, cancellation
epoch, and candidate source commit.

### Install from a release ZIP

The release ZIP has no `.git` directory, so `git rev-parse HEAD` is not a valid
source-identity check inside an extracted archive. Verify the published
SHA-256 sidecar, read the exact full release commit from the GitHub release
notes, and use archive mode:

```powershell
$ZipPath = (Resolve-Path .\codex-coding-os-v1.3.0.zip).Path
$ExpectedZipSha = ((Get-Content "$ZipPath.sha256").Split()[0]).ToLowerInvariant()
$ActualZipSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath).Hash.ToLowerInvariant()
if ($ActualZipSha -ne $ExpectedZipSha) { throw "Release ZIP digest mismatch." }

$ArchiveRoot = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $ArchiveRoot | Out-Null
Expand-Archive -LiteralPath $ZipPath -DestinationPath $ArchiveRoot
Set-Location $ArchiveRoot

$ReleaseCommit = "<full 40-character commit from the v1.3.0 release notes>"
$BundleDigest = (Get-Content .\install-bundle.manifest.json | ConvertFrom-Json).aggregate_sha256
.\scripts\install.ps1 `
  -ArchiveMode `
  -ExpectedSourceCommit $ReleaseCommit `
  -ExpectedBundleSha256 $BundleDigest
```

Archive mode preserves universal policy and cannot install or remove it. Use a
verified tagged Git checkout for `-InstallUniversalPolicy` or
`-RemoveUniversalPolicy`.

## Upgrade from a 0.x installation

Use the current version 1.x source commit and its committed bundle digest. The following
PowerShell command installs the managed universal policy and archives legacy
state only when the legacy directory exists:

```powershell
$InstallArgs = @{
  ExpectedSourceCommit = (git rev-parse HEAD).Trim()
  ExpectedBundleSha256 = (Get-Content .\install-bundle.manifest.json | ConvertFrom-Json).aggregate_sha256
  InstallUniversalPolicy = $true
  PolicyAuthoritySource = "explicit-user-approval"
  PolicyAuthorityReference = "approved-v1.3.0-installation"
}
$LegacyRoot = "$env:USERPROFILE\.codex\case-state"
if (Test-Path -LiteralPath $LegacyRoot -PathType Container) {
  $InstallArgs.ArchiveLegacyState = $true
  $InstallArgs.LegacyStateRoot = $LegacyRoot
}
.\scripts\install.ps1 @InstallArgs
```

Add `-LegacyOverlapMigration` only for an existing strict v2 installation in
the canonical nested layout described above. Do not use it for a clean install
or an existing v3 install.

The upgrade does not import old cases as active campaigns and does not preserve
an executable fallback. Former commands intentionally return
`LEGACY_ENGINE_RETIRED`. Open a fresh Codex task after changing managed rules or
skills so the task reloads the installed policy and capability catalogue. An
already-running task, or a task rooted in a deliberately preserved older Git
branch, can continue to display older task-scoped text without reactivating the
retired engine.

## Enable security plugins

The repository installer does not install or copy Codex-managed plugins. After
the Coding OS install completes:

1. Open Codex Plugins.
2. Install Codex Security.
3. Install Supabase only for a Supabase project.
4. Install Neon Postgres only for a Neon project.
5. Connect only the provider used by the current project.
6. Restart Codex and open a new task.

Use [Security Capability Operating Model](security-capability-operating-model.md)
to select among all 13 Codex Security skills and compose frontend, Supabase,
Neon, or generic PostgreSQL work correctly. The dormant router source in this
repository is not activated by installation.

## Verify the installed runtime

```powershell
$Engine = "$env:USERPROFILE\.codex\coding-os\scripts\agent\campaign_engine\cli.py"
python -B $Engine --json doctor
```

Use `--live-host-probe` when the native Codex host is available and a live
bind-before-turn proof is required.

The default diagnostic runs no model turns. The live probe runs bounded model
turns in a disposable repository. `doctor` checks runtime and store integrity,
router prerequisites, and optionally the native host. It does not verify
validation file access.

Before the first campaign, run the existing paired boundary test from the root
of the verified package source checkout or extracted release ZIP:

```powershell
python -B -m unittest tests.test_campaign_validation_boundary.ValidationBoundaryTests.test_supported_boundary_reads_product_and_denies_engine_state_write -v
```

This test must pass. It reads a disposable product file and verifies that a
write to a separate engine-state marker is denied. A previous successful probe
does not prove that a new project's required tools or acceptance flow work.

## Admit a campaign

Tell the agent the customer outcome, existing project sources, permitted cost
and delivery scope. It should derive file layout, identifiers, test commands,
worktree details, and runtime values. It should ask only about unresolved
customer behavior, priorities, spending, commitments, or consequential actions.
Previously approved choices remain in force until relevant facts change.

The agent can prepare a proposed change before admission:

```powershell
python -B $Engine --json prepare --spec .\proposed.json --repository C:\path\to\project --output .\campaign.json
```

Preparation uses the existing sources and creates no documentation system or
campaign. The proposal must declare each node's source-linked acceptance
scenarios and required tools. See [Campaign Engine Contract](campaign-engine.md)
for those fields and the supported correction policy. The example below is a
technical reference for the agent, not a questionnaire for the founder.

Copy [`templates/campaign.example.json`](../templates/campaign.example.json) and
replace every sample repository, path, commit, runtime-pin, reviewer, deadline,
scope, validation, budget, and publication value with exact admitted values.
Replace the sample `public_key_base64` with the canonical Base64 encoding of an
operator-owned 32-byte Ed25519 public key. Keep its private key outside Git and
outside the installed runtime. Repair and publication authorizations are
one-use external signatures over the engine's canonical receipt. The sample
public key's private half was discarded when this example was created. It
cannot authorize real publication and must still be replaced. The example is
model-valid, but its sample identities are intentionally not admissible against
a real checkout. Then run:

```powershell
python -B $Engine --json admit --spec .\campaign.json
python -B $Engine --json approve --campaign-id <id> --specification-digest <digest>
python -B $Engine --json run --campaign-id <id>
```

The admitted repository root, worktree, remote, branch, base commit, installed
source, bundle, transaction, protocol, schema, host capability, graph, scope,
validation, reviewers, budgets, deadlines, and publication sequence are exact.

## Inspect or stop

```powershell
python -B $Engine --json status --campaign-id <id>
python -B $Engine --json cancel --campaign-id <id>
```

`run` yields at named external events. It never hides an indefinite polling
loop. A cancelled campaign cannot resume automatically after restart.

Omit `--json` from `status` for the business outcome, progress, and reason for
waiting or stopping. Reading status creates no status-only commit or pull
request. A local Git push in a disposable acceptance fixture proves that local
delivery path. It does not prove a merged public release or independent adoption.

## Inspect legacy evidence

```powershell
python -B $Engine --json legacy inspect --source "$env:USERPROFILE\.codex\case-state"
```

Legacy inspection is read-only and cannot create a campaign.
