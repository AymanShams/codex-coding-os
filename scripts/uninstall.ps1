[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
  [string]$SkillsRoot,
  [string]$CodexHome = "$HOME\.codex",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Resolve-InstallPath {
  param([Parameter(Mandatory = $true)][string]$Path)
  $Expanded = [Environment]::ExpandEnvironmentVariables($Path)
  $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Expanded)
}

function Test-IsUnderRoot {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Root
  )
  $ResolvedPath = Resolve-InstallPath $Path
  $ResolvedRoot = Resolve-InstallPath $Root
  $RootWithSlash = $ResolvedRoot.TrimEnd("\") + "\"
  $ResolvedPath.Equals($ResolvedRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $ResolvedPath.StartsWith($RootWithSlash, [System.StringComparison]::OrdinalIgnoreCase)
}

function Remove-IfPresent {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][System.Management.Automation.PSCmdlet]$Cmdlet
  )
  if (Test-Path $Path) {
    if ($DryRun) {
      Write-Output "DRY RUN: would remove $Label at $Path"
    } elseif ($Cmdlet.ShouldProcess($Path, "Remove $Label")) {
      Remove-Item -LiteralPath $Path -Recurse -Force
      Write-Output "Removed ${Label}: $Path"
    }
  }
}

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SkillSourceRoot = Join-Path $RepoRoot ".agents\skills"
$CodexHome = Resolve-InstallPath $CodexHome
$DefaultSupportRoot = Join-Path $CodexHome "coding-os-starter"
$ManifestJsonPath = Join-Path $DefaultSupportRoot "install-manifest.json"
$ManifestTextPath = Join-Path $DefaultSupportRoot "install-manifest.txt"
$Manifest = $null
$TextManifest = @{}
$TextSkillPaths = @()

if (Test-Path $ManifestJsonPath) {
  $Manifest = Get-Content -Raw -LiteralPath $ManifestJsonPath | ConvertFrom-Json
} elseif (Test-Path $ManifestTextPath) {
  foreach ($Line in Get-Content -LiteralPath $ManifestTextPath) {
    if ($Line -match '^([^=]+)=(.*)$') {
      if ($Matches[1] -eq "SkillPath") {
        $TextSkillPaths += $Matches[2]
      } else {
        $TextManifest[$Matches[1]] = $Matches[2]
      }
    }
  }
}

if (-not $SkillsRoot) {
  if ($Manifest -and $Manifest.skills_root) {
    $SkillsRoot = [string]$Manifest.skills_root
  } elseif ($TextManifest.SkillsRoot) {
    $SkillsRoot = [string]$TextManifest.SkillsRoot
  } else {
    $SkillsRoot = "$HOME\.agents\skills"
  }
}

$SkillsRoot = Resolve-InstallPath $SkillsRoot
$InstalledSupportRoot = if ($Manifest -and $Manifest.support_root) {
  Resolve-InstallPath ([string]$Manifest.support_root)
} elseif ($TextManifest.SupportRoot) {
  Resolve-InstallPath ([string]$TextManifest.SupportRoot)
} else {
  $DefaultSupportRoot
}

$AgentsPath = if ($Manifest -and $Manifest.global_agents_path) {
  Resolve-InstallPath ([string]$Manifest.global_agents_path)
} elseif ($TextManifest.GlobalAgentsPath) {
  Resolve-InstallPath ([string]$TextManifest.GlobalAgentsPath)
} else {
  Join-Path $CodexHome "AGENTS.md"
}

$Markers = @{
  Start = "# BEGIN CODEX CODING OS STARTER"
  End = "# END CODEX CODING OS STARTER"
}

$SkillTargets = @()
if ($Manifest -and $Manifest.skills) {
  foreach ($Skill in $Manifest.skills) {
    $Path = [string]$Skill.path
    if ($Path) {
      $SkillTargets += $Path
    }
  }
} elseif ($TextSkillPaths.Count -gt 0) {
  $SkillTargets += $TextSkillPaths
} elseif (Test-Path $SkillSourceRoot) {
  Get-ChildItem -Path $SkillSourceRoot -Directory | Sort-Object Name | ForEach-Object {
    $SkillTargets += (Join-Path $SkillsRoot $_.Name)
  }
}

foreach ($Target in $SkillTargets) {
  $ResolvedTarget = Resolve-InstallPath $Target
  if (-not (Test-IsUnderRoot -Path $ResolvedTarget -Root $SkillsRoot)) {
    throw "Refusing to remove skill outside SkillsRoot. Target=$ResolvedTarget SkillsRoot=$SkillsRoot"
  }
  Remove-IfPresent -Path $ResolvedTarget -Label "skill" -Cmdlet $PSCmdlet
}

if (Test-Path $AgentsPath) {
  $Existing = Get-Content -Raw -LiteralPath $AgentsPath
  $Pattern = "(?s)\r?\n?$([regex]::Escape($Markers.Start)).*?$([regex]::Escape($Markers.End))\r?\n?"
  if ($Existing -match $Pattern) {
    if ($DryRun) {
      Write-Output "DRY RUN: would remove global AGENTS block from $AgentsPath"
    } elseif ($PSCmdlet.ShouldProcess($AgentsPath, "Remove global AGENTS block")) {
      $Backup = "$AgentsPath.bak.$(Get-Date -Format yyyyMMddHHmmss)"
      Copy-Item -LiteralPath $AgentsPath -Destination $Backup
      $Updated = [regex]::Replace($Existing, $Pattern, "")
      Set-Content -LiteralPath $AgentsPath -Value $Updated -Encoding UTF8
      Write-Output "Removed global AGENTS block. Backup: $Backup"
    }
  }
}

if (-not (Test-IsUnderRoot -Path $InstalledSupportRoot -Root $CodexHome)) {
  throw "Refusing to remove support files outside CodexHome. Target=$InstalledSupportRoot CodexHome=$CodexHome"
}
Remove-IfPresent -Path $InstalledSupportRoot -Label "installed support files" -Cmdlet $PSCmdlet

Write-Output "Uninstall complete."
