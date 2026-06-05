param(
  [switch]$InstallGlobalAgents,
  [switch]$InstallExternalSkills,
  [string]$SkillsRoot = "$HOME\.agents\skills",
  [string]$CodexHome = "$HOME\.codex"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SourceSkills = Join-Path $RepoRoot ".agents\skills"
$ManifestDir = Join-Path $CodexHome "coding-os-starter"
$ManifestPath = Join-Path $ManifestDir "install-manifest.txt"

if (-not (Test-Path $SourceSkills)) {
  throw "Cannot find source skills folder: $SourceSkills"
}

New-Item -ItemType Directory -Force -Path $SkillsRoot | Out-Null
New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null
New-Item -ItemType Directory -Force -Path $ManifestDir | Out-Null

$Installed = @()
Get-ChildItem -Path $SourceSkills -Directory | ForEach-Object {
  $Target = Join-Path $SkillsRoot $_.Name
  if (Test-Path $Target) {
    $Backup = "$Target.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -Path $Target -Destination $Backup -Recurse -Force
  }
  Copy-Item -Path $_.FullName -Destination $Target -Recurse -Force
  $Installed += $Target
}

$SupportItems = @(
  "templates",
  "docs",
  "codex-capabilities",
  "external-skills",
  "hooks",
  "patches",
  "scripts",
  "THIRD_PARTY_SKILLS.md",
  "README.md",
  "LICENSE.md",
  "COMMERCIAL.md",
  "NOTICE.md"
)

foreach ($Item in $SupportItems) {
  $Source = Join-Path $RepoRoot $Item
  if (Test-Path $Source) {
    $Target = Join-Path $ManifestDir $Item
    if (Test-Path $Target) {
      $Backup = "$Target.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
      Copy-Item -Path $Target -Destination $Backup -Recurse -Force
      Remove-Item -LiteralPath $Target -Recurse -Force
    }
    Copy-Item -Path $Source -Destination $Target -Recurse -Force
  }
}

if ($InstallGlobalAgents) {
  $GlobalAgents = Join-Path $CodexHome "AGENTS.md"
  $PackAgents = Join-Path $RepoRoot "AGENTS.md"
  $Start = "# BEGIN CODEX CODING OS STARTER"
  $End = "# END CODEX CODING OS STARTER"
  $PackText = Get-Content -Raw -Path $PackAgents
  $Block = "$Start`r`n$PackText`r`n$End"

  if (Test-Path $GlobalAgents) {
    $Existing = Get-Content -Raw -Path $GlobalAgents
    $Backup = "$GlobalAgents.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -Path $GlobalAgents -Destination $Backup -Force
    $Pattern = "(?s)# BEGIN CODEX CODING OS STARTER.*?# END CODEX CODING OS STARTER"
    if ($Existing -match $Pattern) {
      $Updated = [regex]::Replace($Existing, $Pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $Block })
    } else {
      $Updated = $Existing.TrimEnd() + "`r`n`r`n" + $Block + "`r`n"
    }
    Set-Content -Path $GlobalAgents -Value $Updated -Encoding UTF8
  } else {
    Set-Content -Path $GlobalAgents -Value ($Block + "`r`n") -Encoding UTF8
  }
}

$Lines = @()
$Lines += "Codex Coding OS Starter installed at $(Get-Date -Format o)"
$Lines += "RepoRoot=$RepoRoot"
$Lines += "SkillsRoot=$SkillsRoot"
$Lines += "InstalledGlobalAgents=$InstallGlobalAgents"
$Lines += "InstalledExternalSkills=$InstallExternalSkills"
$Lines += "Skills:"
$Lines += $Installed
Set-Content -Path $ManifestPath -Value $Lines -Encoding UTF8

if ($InstallExternalSkills) {
  & (Join-Path $RepoRoot "scripts\install-external-skills.ps1") -Install forrestchang-andrej-karpathy-skills -ApplyOverlays -TargetSkillsRoot $SkillsRoot
}

Write-Output "Installed skills:"
$Installed | ForEach-Object { Write-Output " - $_" }
if ($InstallGlobalAgents) {
  Write-Output "Updated global AGENTS.md with backup if needed."
}
Write-Output "Support files copied to: $ManifestDir"
Write-Output "Restart Codex, then use templates\first-codex-prompt.md from this repo or $ManifestDir."
