param(
  [switch]$RequireExternalScanners
)

$ErrorActionPreference = "Stop"

function Convert-PackPath {
  param([Parameter(Mandatory = $true)][string]$Path)
  $Path -replace "/", [System.IO.Path]::DirectorySeparatorChar
}

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ManifestPath = Join-Path $RepoRoot "pack.manifest.json"
$Errors = @()

if (-not (Test-Path $ManifestPath)) {
  Write-Output "Validation failed:"
  Write-Output " - Missing pack.manifest.json"
  exit 1
}

try {
  $Manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
} catch {
  Write-Output "Validation failed:"
  Write-Output " - pack.manifest.json is not valid JSON: $($_.Exception.Message)"
  exit 1
}

foreach ($Path in $Manifest.required_files) {
  $Full = Join-Path $RepoRoot (Convert-PackPath $Path)
  if (-not (Test-Path $Full)) {
    $Errors += "Missing required file: $Path"
  }
}

$SkillRoot = Join-Path $RepoRoot ".agents\skills"
if (-not (Test-Path $SkillRoot)) {
  $Errors += "Missing bundled skill root: .agents/skills"
} else {
  foreach ($Skill in $Manifest.bundled_skills) {
    $SkillName = [string]$Skill.name
    $SkillFile = Join-Path $SkillRoot "$SkillName\SKILL.md"
    if (-not (Test-Path $SkillFile)) {
      $Errors += "Missing bundled full skill: $SkillName"
    }
  }

  Get-ChildItem -Path $SkillRoot -Directory | ForEach-Object {
    $SkillFile = Join-Path $_.FullName "SKILL.md"
    if (-not (Test-Path $SkillFile)) {
      $Errors += "Missing SKILL.md in $($_.FullName)"
    } else {
      $Text = Get-Content -Raw -LiteralPath $SkillFile
      if ($Text -notmatch "(?s)^---.*name:.*description:.*---") {
        $Errors += "Missing frontmatter fields in $SkillFile"
      }
    }
  }
}

$ExternalManifestPath = Join-Path $RepoRoot "external-skills\manifest.json"
if (Test-Path $ExternalManifestPath) {
  try {
    $ExternalManifest = Get-Content -Raw -LiteralPath $ExternalManifestPath | ConvertFrom-Json
    foreach ($Source in $ExternalManifest.sources) {
      if (-not $Source.id) { $Errors += "External source missing id." }
      if (-not $Source.repo) { $Errors += "External source missing repo: $($Source.id)" }
      if (-not $Source.treatment) { $Errors += "External source missing treatment: $($Source.id)" }
      if (-not $Source.license) { $Errors += "External source missing license metadata: $($Source.id)" }
      if (-not $Source.reviewed_at) { $Errors += "External source missing reviewed_at: $($Source.id)" }
      if ($null -eq $Source.pinned_commit) { $Errors += "External source missing pinned_commit field: $($Source.id)" }
      if ($null -eq $Source.sha256) { $Errors += "External source missing sha256 field: $($Source.id)" }
      if (-not $Source.pin_status) { $Errors += "External source missing pin_status: $($Source.id)" }
      if ($Source.treatment -match "optional-install" -and [string]::IsNullOrWhiteSpace([string]$Source.pinned_commit)) {
        if ($Source.pin_status -ne "required-before-repeatable-install") {
          $Errors += "Installable external source without pinned_commit must set pin_status=required-before-repeatable-install: $($Source.id)"
        }
      }
      if ($Source.treatment -eq "reference-only" -and $Source.pin_status -ne "reference-only-not-installed") {
        $Errors += "Reference-only external source must set pin_status=reference-only-not-installed: $($Source.id)"
      }
    }
  } catch {
    $Errors += "external-skills/manifest.json is not valid JSON: $($_.Exception.Message)"
  }
}

if ($Errors.Count -eq 0) {
  $SafetyScript = Join-Path $RepoRoot "scripts\release-safety-scan.ps1"
  if (Test-Path $SafetyScript) {
    if ($RequireExternalScanners) {
      & $SafetyScript -RepoRoot $RepoRoot -RequireExternalScanners
    } else {
      & $SafetyScript -RepoRoot $RepoRoot
    }
    if (-not $?) {
      $Errors += "Release safety scan failed."
    }
  } else {
    $Errors += "Missing release safety scan script."
  }
}

if ($Errors.Count -gt 0) {
  Write-Output "Validation failed:"
  $Errors | ForEach-Object { Write-Output " - $_" }
  exit 1
}

Write-Output "Validation passed."
