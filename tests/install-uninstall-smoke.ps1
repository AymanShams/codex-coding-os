$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$TestRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ccos-transaction-smoke-" + [guid]::NewGuid().ToString("N"))
$SkillsRoot = Join-Path $TestRoot "skills"
$CodexHome = Join-Path $TestRoot "codex-home"
$InstallScript = Join-Path $RepoRoot "scripts\install.ps1"
$UninstallScript = Join-Path $RepoRoot "scripts\uninstall.ps1"
$Bundle = Get-Content -Raw -LiteralPath (Join-Path $RepoRoot "install-bundle.manifest.json") | ConvertFrom-Json
$BundleHash = [string]$Bundle.aggregate_sha256

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

  & $InstallScript -SkillsRoot $SkillsRoot -CodexHome $CodexHome -ExpectedBundleSha256 $BundleHash -ArchiveMode -Confirm:$false
  if (-not $?) { throw "Transactional install failed." }

  $ManifestPath = Join-Path $CodexHome "coding-os\install-manifest.json"
  $CurrentPath = Join-Path $CodexHome ".coding-os-install\current.json"
  $Manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
  $Current = Get-Content -Raw -LiteralPath $CurrentPath | ConvertFrom-Json
  if ($Manifest.manifest_version -ne 3) { throw "V3 install manifest was not written." }
  if ($Manifest.transaction_protocol -ne "ccos-install-transaction-v1") { throw "Transaction protocol mismatch." }
  if ($Manifest.package.bundle_sha256 -ne $BundleHash) { throw "Bundle provenance mismatch." }
  if ($Current.status -ne "committed") { throw "Current pointer was not committed." }
  if (-not (Test-Path (Join-Path $SkillsRoot "codex-coding-os-master\SKILL.md"))) { throw "Managed skill was not installed." }

  $PointerBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $CurrentPath).Hash
  & $InstallScript -SkillsRoot $SkillsRoot -CodexHome $CodexHome -ExpectedBundleSha256 $BundleHash -ArchiveMode -Confirm:$false
  if (-not $?) { throw "Transactional idempotent reinstall failed." }
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath $CurrentPath).Hash -ne $PointerBefore) { throw "Idempotent reinstall changed current.json." }

  foreach ($Path in $Preserved.Keys) {
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash -ne $Preserved[$Path]) {
      throw "Preserved path changed: $Path"
    }
  }

  & $UninstallScript -SkillsRoot $SkillsRoot -CodexHome $CodexHome -Confirm:$false
  if (-not $?) { throw "Transactional uninstall failed." }
  if (Test-Path (Join-Path $CodexHome "coding-os")) { throw "Managed support root remained after uninstall." }
  if (Test-Path (Join-Path $SkillsRoot "codex-coding-os-master")) { throw "Managed skill remained after uninstall." }
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
