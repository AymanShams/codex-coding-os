[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
  [switch]$InstallUniversalPolicy,
  [string]$UniversalBundleId = "campaign-engine-policy-v1",
  [switch]$RefreshCapabilityIndex,
  [string]$CodexHome = "$HOME\.codex",
  [string]$SkillsRoot,
  [Parameter(Mandatory = $true)][string]$ExpectedBundleSha256,
  [Parameter(Mandatory = $true)][string]$ExpectedSourceCommit,
  [ValidateSet("explicit-user-approval", "campaign-publication-authority")][string]$PolicyAuthoritySource,
  [string]$PolicyAuthorityReference,
  [string]$PublicationCampaignId,
  [string]$PublicationNodeId,
  [int]$PublicationAuthorityEpoch,
  [int]$PublicationCancellationEpoch = -1,
  [switch]$ArchiveLegacyState,
  [string]$LegacyStateRoot,
  [switch]$LegacyOverlapMigration,
  [switch]$ArchiveMode,
  [switch]$InstallExternalSkills,
  [switch]$AllowUnpinnedExternalSkills,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Engine = Join-Path $RepoRoot "scripts\install_transaction.py"
if (-not $SkillsRoot) { $SkillsRoot = Join-Path $CodexHome "skills" }

if ($InstallExternalSkills -or $AllowUnpinnedExternalSkills) {
  throw "Optional external skills are not enabled in the transactional public package. Install only a separately reviewed pinned bundle."
}
if (-not (Test-Path -LiteralPath $Engine -PathType Leaf)) {
  throw "Transactional install engine is missing: $Engine"
}
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
  $Python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $Python) {
  throw "Python 3 is required for the transactional installer."
}

$Arguments = @(
  "-B", $Engine, "--json", "install",
  "--source-root", $RepoRoot,
  "--skills-root", $SkillsRoot,
  "--codex-home", $CodexHome,
  "--expected-bundle-sha256", $ExpectedBundleSha256,
  "--universal-bundle-id", $UniversalBundleId
)
$Arguments += @("--expected-source-commit", $ExpectedSourceCommit)
if ($InstallUniversalPolicy) { $Arguments += "--install-universal-policy" }
if ($RefreshCapabilityIndex) { $Arguments += "--refresh-capability-index" }
if ($PolicyAuthoritySource) { $Arguments += @("--policy-authority-source", $PolicyAuthoritySource) }
if ($PolicyAuthorityReference) { $Arguments += @("--policy-authority-reference", $PolicyAuthorityReference) }
if ($PublicationCampaignId) { $Arguments += @("--publication-campaign-id", $PublicationCampaignId) }
if ($PublicationNodeId) { $Arguments += @("--publication-node-id", $PublicationNodeId) }
if ($PublicationAuthorityEpoch -gt 0) { $Arguments += @("--publication-authority-epoch", [string]$PublicationAuthorityEpoch) }
if ($PublicationCancellationEpoch -ge 0) { $Arguments += @("--publication-cancellation-epoch", [string]$PublicationCancellationEpoch) }
if ($ArchiveLegacyState) { $Arguments += "--archive-legacy-state" }
if ($LegacyStateRoot) { $Arguments += @("--legacy-state-root", $LegacyStateRoot) }
if ($ArchiveMode) { $Arguments += "--archive-mode" }
if ($LegacyOverlapMigration) { $Arguments += "--legacy-overlap-migration" }
if ($DryRun) { $Arguments += "--dry-run" }

$TargetDescription = "SkillsRoot=$SkillsRoot; CodexHome=$CodexHome; Bundle=$ExpectedBundleSha256"
if ($DryRun -or $PSCmdlet.ShouldProcess($TargetDescription, "Run one transactional Codex Coding OS install")) {
  if ($Python.Name -eq "py.exe" -or $Python.Name -eq "py") {
    & $Python.Source -3 @Arguments
  } else {
    & $Python.Source @Arguments
  }
  if ($LASTEXITCODE -ne 0) {
    throw "Transactional install failed with exit code $LASTEXITCODE."
  }
}
