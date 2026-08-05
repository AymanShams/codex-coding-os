[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
  [string]$CodexHome = "$HOME\.codex",
  [string]$SkillsRoot,
  [switch]$LegacyOverlapMigration,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Engine = Join-Path $RepoRoot "scripts\install_transaction.py"
if (-not $SkillsRoot) { $SkillsRoot = Join-Path $CodexHome "skills" }
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { $Python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $Python) { throw "Python 3 is required for the transactional uninstaller." }
if (-not (Test-Path -LiteralPath $Engine -PathType Leaf)) { throw "Transactional uninstall engine is missing: $Engine" }

$Arguments = @(
  "-B", $Engine, "--json", "uninstall",
  "--skills-root", $SkillsRoot,
  "--codex-home", $CodexHome
)
if ($LegacyOverlapMigration) { $Arguments += "--legacy-overlap-migration" }
if ($DryRun) { $Arguments += "--dry-run" }
$TargetDescription = "SkillsRoot=$SkillsRoot; CodexHome=$CodexHome"
if ($DryRun -or $PSCmdlet.ShouldProcess($TargetDescription, "Run one transactional Codex Coding OS uninstall")) {
  if ($Python.Name -eq "py.exe" -or $Python.Name -eq "py") {
    & $Python.Source -3 @Arguments
  } else {
    & $Python.Source @Arguments
  }
  if ($LASTEXITCODE -ne 0) { throw "Transactional uninstall failed with exit code $LASTEXITCODE." }
}
