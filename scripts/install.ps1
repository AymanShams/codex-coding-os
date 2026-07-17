[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
  [Alias("InstallGlobalAgents")][switch]$InstallUniversalPolicy,
  [string]$UniversalBundleId = "automation-preserving-case-state-recovery-v1",
  [switch]$RefreshCapabilityIndex,
  [string]$SkillsRoot = "$HOME\.agents\skills",
  [string]$CodexHome = "$HOME\.codex",
  [Parameter(Mandatory = $true)][string]$ExpectedBundleSha256,
  [string]$ExpectedSourceCommit,
  [string]$AuthorityCaseId,
  [ValidateSet("preauthorized-run-envelope", "explicit-user-approval")][string]$AuthoritySource,
  [string]$AuthorityReference,
  [string]$CaseStateEnginePath,
  [string]$CaseStateRoot,
  [switch]$LegacyOverlapMigration,
  [switch]$ArchiveMode,
  [switch]$InstallExternalSkills,
  [switch]$AllowUnpinnedExternalSkills,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Engine = Join-Path $RepoRoot "scripts\install_transaction.py"

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
if ($ExpectedSourceCommit) { $Arguments += @("--expected-source-commit", $ExpectedSourceCommit) }
if ($InstallUniversalPolicy) { $Arguments += "--install-universal-policy" }
if ($RefreshCapabilityIndex) { $Arguments += "--refresh-capability-index" }
if ($AuthorityCaseId) { $Arguments += @("--authority-case-id", $AuthorityCaseId) }
if ($AuthoritySource) { $Arguments += @("--authority-source", $AuthoritySource) }
if ($AuthorityReference) { $Arguments += @("--authority-reference", $AuthorityReference) }
if (-not $CaseStateEnginePath) { $CaseStateEnginePath = Join-Path $RepoRoot "scripts\agent\case_state.py" }
if (-not $CaseStateRoot) { $CaseStateRoot = Join-Path $CodexHome "case-state" }
if ($InstallUniversalPolicy) {
  $Arguments += @("--case-state-engine", $CaseStateEnginePath, "--case-state-root", $CaseStateRoot)
}
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
