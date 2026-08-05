# Getting Started

## Install an exact source commit

Use a clean checkout at the commit you intend to install:

```powershell
git status -sb
.\scripts\validate-pack.ps1
$SourceCommit = git rev-parse HEAD
.\scripts\package.ps1
$BundleDigest = (Get-Content .\install-bundle.manifest.json | ConvertFrom-Json).aggregate_sha256
.\scripts\install.ps1 -ExpectedSourceCommit $SourceCommit -ExpectedBundleSha256 $BundleDigest
```

The install transaction verifies the bundle, atomically promotes managed files,
installs the campaign hook, initializes the external store, and records the six
runtime-pin fields.

The default layout is `%USERPROFILE%\.codex` for `CodexHome` and
`%USERPROFILE%\.codex\skills` for `SkillsRoot`. Clean first installs and v3
reinstalls or uninstalls need no overlap or migration option. Pass
`-LegacyOverlapMigration` only when upgrading an existing strict v2 install in
that exact nested layout. All other overlapping root layouts remain invalid.

The complete `runtime_pin` records source commit, bundle digest, install
transaction, protocol version, schema compatibility, and host capability probe
version. The source runtime record and installed bundle must agree with every
field.

Use `-ArchiveLegacyState` only when the unchanged legacy source at
`$env:USERPROFILE\.codex\case-state` should be ingested as read-only evidence.
Use `-InstallUniversalPolicy` only with an explicit authority source and
reference. Campaign publication authority also requires the exact campaign ID,
node ID, authority epoch, cancellation epoch, and candidate source commit.

## Verify the installed runtime

```powershell
$Engine = "$env:USERPROFILE\.codex\coding-os\scripts\agent\campaign_engine\cli.py"
python $Engine --json doctor
```

Use `--live-host-probe` when the native Codex host is available and a live
bind-before-turn proof is required.

## Admit a campaign

Create a complete specification JSON, then run:

```powershell
python $Engine --json admit --spec .\campaign.json
python $Engine --json approve --campaign-id <id> --specification-digest <digest>
python $Engine --json run --campaign-id <id>
```

The admitted repository root, worktree, remote, branch, base commit, installed
source, bundle, transaction, protocol, schema, host capability, graph, scope,
validation, reviewers, budgets, deadlines, and publication sequence are exact.

## Inspect or stop

```powershell
python $Engine --json status --campaign-id <id>
python $Engine --json cancel --campaign-id <id>
```

`run` yields at named external events. It never hides an indefinite polling
loop. A cancelled campaign cannot resume automatically after restart.

## Inspect legacy evidence

```powershell
python $Engine --json legacy inspect --source "$env:USERPROFILE\.codex\case-state"
```

Legacy inspection is read-only and cannot create a campaign.
