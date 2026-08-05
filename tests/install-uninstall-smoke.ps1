$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$TestRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ccos-transaction-smoke-" + [guid]::NewGuid().ToString("N"))
$ProfileRoot = Join-Path $TestRoot "profile"
$CodexHome = Join-Path $ProfileRoot ".codex"
$SkillsRoot = Join-Path $CodexHome "skills"
$InstallScript = Join-Path $RepoRoot "scripts\install.ps1"
$Engine = Join-Path $RepoRoot "scripts\install_transaction.py"
$Bundle = Get-Content -Raw -LiteralPath (Join-Path $RepoRoot "install-bundle.manifest.json") | ConvertFrom-Json
$BundleHash = [string]$Bundle.aggregate_sha256
$SourceCommit = (& git -C $RepoRoot rev-parse HEAD).Trim()
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { $Python = Get-Command py -ErrorAction Stop }

function Invoke-Transaction {
  param([Parameter(Mandatory = $true)][string[]]$TransactionArguments)

  $Arguments = @("-B", $Engine, "--json") + $TransactionArguments
  if ($Python.Name -eq "py.exe" -or $Python.Name -eq "py") {
    & $Python.Source -3 @Arguments
  } else {
    & $Python.Source @Arguments
  }
  if ($LASTEXITCODE -ne 0) {
    throw "Transactional engine failed with exit code $LASTEXITCODE."
  }
}

try {
  New-Item -ItemType Directory -Force -Path $SkillsRoot, $CodexHome | Out-Null
  Set-Content -LiteralPath (Join-Path $CodexHome "config.toml") -Value "preserved-config" -NoNewline -Encoding utf8
  New-Item -ItemType Directory -Force -Path (Join-Path $CodexHome "case-state"), (Join-Path $CodexHome "plugins"), (Join-Path $SkillsRoot "unmanaged") | Out-Null
  Set-Content -LiteralPath (Join-Path $CodexHome "case-state\case.json") -Value "preserved-case" -NoNewline -Encoding utf8
  Set-Content -LiteralPath (Join-Path $CodexHome "plugins\plugin.txt") -Value "preserved-plugin" -NoNewline -Encoding utf8
  Set-Content -LiteralPath (Join-Path $SkillsRoot "unmanaged\SKILL.md") -Value "preserved-skill" -NoNewline -Encoding utf8
  $Preserved = @{}
  foreach ($Path in @(
    (Join-Path $CodexHome "config.toml"),
    (Join-Path $CodexHome "case-state\case.json"),
    (Join-Path $CodexHome "plugins\plugin.txt"),
    (Join-Path $SkillsRoot "unmanaged\SKILL.md")
  )) {
    $Preserved[$Path] = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
  }

  $Rejected = $false
  try {
    & $InstallScript -CodexHome $CodexHome -ExpectedBundleSha256 $BundleHash -ExpectedSourceCommit $SourceCommit -ArchiveMode -Confirm:$false
  } catch {
    if ($_.Exception.Message -notlike "*canonical operating-system account-profile path*") {
      throw
    }
    $Rejected = $true
  }
  if (-not $Rejected) { throw "Public PowerShell installer accepted a noncanonical CodexHome." }

  $SkillsRejected = $false
  try {
    & $InstallScript -SkillsRoot $SkillsRoot -ExpectedBundleSha256 $BundleHash -ExpectedSourceCommit $SourceCommit -ArchiveMode -Confirm:$false
  } catch {
    if ($_.Exception.Message -notlike "*canonical CodexHome skills path*") {
      throw
    }
    $SkillsRejected = $true
  }
  if (-not $SkillsRejected) { throw "Public PowerShell installer accepted a noncanonical SkillsRoot." }

  Invoke-Transaction -TransactionArguments @(
    "install",
    "--source-root", $RepoRoot,
    "--skills-root", $SkillsRoot,
    "--codex-home", $CodexHome,
    "--expected-bundle-sha256", $BundleHash,
    "--expected-source-commit", $SourceCommit,
    "--archive-mode"
  )

  $ManifestPath = Join-Path $CodexHome "coding-os\install-manifest.json"
  $CurrentPath = Join-Path $CodexHome ".coding-os-install\current.json"
  $Manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
  $Current = Get-Content -Raw -LiteralPath $CurrentPath | ConvertFrom-Json
  if ($Manifest.manifest_version -ne 3) { throw "V3 install manifest was not written." }
  if ($Manifest.transaction_protocol -ne "ccos-install-transaction-v1") { throw "Transaction protocol mismatch." }
  if ($Manifest.package.bundle_sha256 -ne $BundleHash) { throw "Bundle provenance mismatch." }
  if ($Manifest.runtime_pin.source_commit -ne $SourceCommit) { throw "Runtime source pin mismatch." }
  if ($Manifest.runtime_pin.bundle_digest -ne $BundleHash) { throw "Runtime bundle pin mismatch." }
  if ($Manifest.runtime_pin.install_transaction -ne $Manifest.transaction.id) { throw "Runtime transaction pin mismatch." }
  if ($Manifest.runtime_pin.protocol_version -ne "ccos-campaign-v1") { throw "Campaign protocol pin mismatch." }
  if ($Manifest.runtime_pin.schema_compatibility -ne "campaign-store-v1") { throw "Schema compatibility pin mismatch." }
  if ($Manifest.runtime_pin.host_capability_probe_version -ne "native-bind-before-turn-scoped-tools-v3") { throw "Host capability pin mismatch." }
  if ([System.IO.Path]::GetFullPath([string]$Manifest.targets.skills_root) -ne [System.IO.Path]::GetFullPath($SkillsRoot)) { throw "Canonical nested SkillsRoot mismatch." }
  if ($Manifest.PSObject.Properties.Name -contains "legacy_overlap_migration") { throw "Clean canonical install was misclassified as a legacy migration." }
  if ($Current.status -ne "committed") { throw "Current pointer was not committed." }
  if (-not (Test-Path (Join-Path $SkillsRoot "codex-coding-os-master\SKILL.md"))) { throw "Managed skill was not installed." }
  if (-not (Test-Path (Join-Path $CodexHome "hooks\campaign-engine\campaign_hook.py"))) { throw "Campaign hook was not installed." }
  if (-not (Test-Path (Join-Path $CodexHome "coding-os-state\campaigns.sqlite3"))) { throw "Campaign state store was not initialized." }

  $DoctorCode = @'
import pathlib, sys
profile = pathlib.Path(sys.argv[1]).resolve(strict=True)
support = profile / ".codex" / "coding-os"
sys.path.insert(0, str(support / "scripts" / "agent"))
from campaign_engine import cli
from campaign_engine.runtime_bootstrap import runtime_layout
raise SystemExit(cli.main(["--json", "doctor"], injected_runtime=runtime_layout(profile=profile)))
'@
  if ($Python.Name -eq "py.exe" -or $Python.Name -eq "py") {
    $DoctorOutput = $DoctorCode | & $Python.Source -3 -B - $ProfileRoot
  } else {
    $DoctorOutput = $DoctorCode | & $Python.Source -B - $ProfileRoot
  }
  if ($LASTEXITCODE -ne 0) {
    throw "Installed campaign-engine doctor failed: $($DoctorOutput -join [Environment]::NewLine)"
  }
  $Doctor = $DoctorOutput | ConvertFrom-Json
  if (-not $Doctor.ok -or $Doctor.integrity.status -ne "ok") { throw "Installed campaign-engine doctor returned invalid evidence." }

  $PointerBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $CurrentPath).Hash
  Invoke-Transaction -TransactionArguments @(
    "install",
    "--source-root", $RepoRoot,
    "--skills-root", $SkillsRoot,
    "--codex-home", $CodexHome,
    "--expected-bundle-sha256", $BundleHash,
    "--expected-source-commit", $SourceCommit,
    "--archive-mode"
  )
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath $CurrentPath).Hash -ne $PointerBefore) { throw "Idempotent reinstall changed current.json." }

  foreach ($Path in $Preserved.Keys) {
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash -ne $Preserved[$Path]) {
      throw "Preserved path changed: $Path"
    }
  }

  Invoke-Transaction -TransactionArguments @(
    "uninstall",
    "--skills-root", $SkillsRoot,
    "--codex-home", $CodexHome
  )
  if (Test-Path (Join-Path $CodexHome "coding-os")) { throw "Managed support root remained after uninstall." }
  if (Test-Path (Join-Path $SkillsRoot "codex-coding-os-master")) { throw "Managed skill remained after uninstall." }
  if (Test-Path (Join-Path $CodexHome "hooks\campaign-engine")) { throw "Managed campaign hook remained after uninstall." }
  if (-not (Test-Path (Join-Path $CodexHome "coding-os-state\campaigns.sqlite3"))) { throw "Uninstall removed external campaign state." }
  foreach ($Path in $Preserved.Keys) {
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash -ne $Preserved[$Path]) {
      throw "Uninstall changed preserved path: $Path"
    }
  }
  $Uninstalled = Get-Content -Raw -LiteralPath $CurrentPath | ConvertFrom-Json
  if ($Uninstalled.status -ne "uninstalled") { throw "Uninstall pointer was not committed." }

  Write-Output "Transactional PowerShell install/uninstall smoke test passed."
} finally {
  if (Test-Path $TestRoot) {
    Remove-Item -LiteralPath $TestRoot -Recurse -Force
  }
}
