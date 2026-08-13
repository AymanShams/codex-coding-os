# Getting Started

## Install an exact source commit

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
$ZipPath = (Resolve-Path .\codex-coding-os-v1.2.1.zip).Path
$ExpectedZipSha = ((Get-Content "$ZipPath.sha256").Split()[0]).ToLowerInvariant()
$ActualZipSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath).Hash.ToLowerInvariant()
if ($ActualZipSha -ne $ExpectedZipSha) { throw "Release ZIP digest mismatch." }

$ArchiveRoot = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $ArchiveRoot | Out-Null
Expand-Archive -LiteralPath $ZipPath -DestinationPath $ArchiveRoot
Set-Location $ArchiveRoot

$ReleaseCommit = "<full 40-character commit from the v1.2.1 release notes>"
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
  PolicyAuthorityReference = "approved-v1.2.1-installation"
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

## Admit a campaign

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

## Inspect legacy evidence

```powershell
python -B $Engine --json legacy inspect --source "$env:USERPROFILE\.codex\case-state"
```

Legacy inspection is read-only and cannot create a campaign.
