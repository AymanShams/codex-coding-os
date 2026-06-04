param(
  [string]$TargetSkillsRoot = "$HOME\.agents\skills"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$OverlayRoot = Join-Path $RepoRoot "patches\external-skills"

if (-not (Test-Path $TargetSkillsRoot)) {
  throw "Target skills root does not exist: $TargetSkillsRoot"
}

$Applied = @()

$KarpathyOverlay = Join-Path $OverlayRoot "forrestchang-andrej-karpathy-skills\CODING_OS_OVERLAY.md"
$CandidateDirs = Get-ChildItem -Path $TargetSkillsRoot -Directory | Where-Object {
  $_.Name -match "karpathy|andrej|ai-coding|claude"
}

foreach ($Dir in $CandidateDirs) {
  $Target = Join-Path $Dir.FullName "CODING_OS_OVERLAY.md"
  Copy-Item -Path $KarpathyOverlay -Destination $Target -Force
  $Applied += $Target
}

if ($Applied.Count -eq 0) {
  Write-Output "No likely external Karpathy-inspired skill folder found. No overlays applied."
} else {
  Write-Output "Applied overlays:"
  $Applied | ForEach-Object { Write-Output " - $_" }
}

