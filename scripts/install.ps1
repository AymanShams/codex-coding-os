[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
  [switch]$InstallGlobalAgents,
  [switch]$InstallExternalSkills,
  [string]$SkillsRoot = "$HOME\.agents\skills",
  [string]$CodexHome = "$HOME\.codex",
  [switch]$AllowUnpinnedExternalSkills,
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
  $RootWithSlash = $ResolvedRoot.TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar
  $ResolvedPath.Equals($ResolvedRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $ResolvedPath.StartsWith($RootWithSlash, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-CommandAvailable {
  param([Parameter(Mandatory = $true)][string]$Name)
  [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Convert-PackPath {
  param([Parameter(Mandatory = $true)][string]$Path)
  $Path -replace "/", [System.IO.Path]::DirectorySeparatorChar
}

function Invoke-CopyDirectoryClean {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Target,
    [Parameter(Mandatory = $true)][string]$Label
  )

  if (Test-Path $Target) {
    $Backup = "$Target.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    if ($DryRun) {
      Write-Output "DRY RUN: would back up $Label to $Backup"
      Write-Output "DRY RUN: would remove existing $Label at $Target"
    } else {
      Copy-Item -LiteralPath $Target -Destination $Backup -Recurse -Force
      Remove-Item -LiteralPath $Target -Recurse -Force
    }
  }

  if ($DryRun) {
    Write-Output "DRY RUN: would copy $Label from $Source to $Target"
  } else {
    Copy-Item -LiteralPath $Source -Destination $Target -Recurse -Force
  }
}

function Invoke-CopyFileClean {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Target,
    [Parameter(Mandatory = $true)][string]$Label
  )

  if (Test-Path $Target) {
    $Backup = "$Target.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    if ($DryRun) {
      Write-Output "DRY RUN: would back up $Label to $Backup"
    } else {
      Copy-Item -LiteralPath $Target -Destination $Backup -Force
      Remove-Item -LiteralPath $Target -Force
    }
  }

  if ($DryRun) {
    Write-Output "DRY RUN: would copy $Label from $Source to $Target"
  } else {
    Copy-Item -LiteralPath $Source -Destination $Target -Force
  }
}

function Get-UpdatedAgentsText {
  param(
    [Parameter(Mandatory = $true)][string]$Existing,
    [Parameter(Mandatory = $true)][string]$Block
  )
  $Pattern = "(?s)# BEGIN CODEX CODING OS(?: STARTER)?.*?# END CODEX CODING OS(?: STARTER)?"
  if ($Existing -match $Pattern) {
    [regex]::Replace($Existing, $Pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $Block })
  } else {
    $Existing.TrimEnd() + "`r`n`r`n" + $Block + "`r`n"
  }
}

if ($PSVersionTable.PSVersion.Major -lt 5) {
  throw "PowerShell 5.1 or later is required."
}

if ($InstallExternalSkills -and -not (Test-CommandAvailable -Name "git")) {
  throw "Git is required when -InstallExternalSkills is used."
}

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SourceSkills = Join-Path $RepoRoot ".agents\skills"
$PackManifestPath = Join-Path $RepoRoot "pack.manifest.json"
$PackManifest = $null
if (Test-Path $PackManifestPath) {
  $PackManifest = Get-Content -Raw -LiteralPath $PackManifestPath | ConvertFrom-Json
}
$SkillsRoot = Resolve-InstallPath $SkillsRoot
$CodexHome = Resolve-InstallPath $CodexHome
$ManifestDir = Join-Path $CodexHome "coding-os"
$LegacyManifestDir = Join-Path $CodexHome ("coding-os" + "-starter")
$ManifestJsonPath = Join-Path $ManifestDir "install-manifest.json"
$ManifestTextPath = Join-Path $ManifestDir "install-manifest.txt"
$PreviousManifestJsonPath = if (Test-Path $ManifestJsonPath) {
  $ManifestJsonPath
} elseif (Test-Path (Join-Path $LegacyManifestDir "install-manifest.json")) {
  Join-Path $LegacyManifestDir "install-manifest.json"
} else {
  $ManifestJsonPath
}
$PreviousManifest = $null
if (Test-Path $PreviousManifestJsonPath) {
  try {
    $PreviousManifest = Get-Content -Raw -LiteralPath $PreviousManifestJsonPath | ConvertFrom-Json
  } catch {
    Write-Warning "Previous JSON install manifest could not be read. Obsolete skill cleanup will be skipped."
  }
}

if (-not (Test-Path $SourceSkills)) {
  throw "Cannot find source skills folder: $SourceSkills"
}

if ($DryRun) {
  Write-Output "DRY RUN: installation paths"
  Write-Output " - SkillsRoot: $SkillsRoot"
  Write-Output " - CodexHome: $CodexHome"
  Write-Output " - Support files: $ManifestDir"
} else {
  New-Item -ItemType Directory -Force -Path $SkillsRoot | Out-Null
  New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null
  New-Item -ItemType Directory -Force -Path $ManifestDir | Out-Null
}

$Installed = @()
Get-ChildItem -Path $SourceSkills -Directory | Sort-Object Name | ForEach-Object {
  $Target = Join-Path $SkillsRoot $_.Name
  if ($PSCmdlet.ShouldProcess($Target, "Install skill $($_.Name)")) {
    Invoke-CopyDirectoryClean -Source $_.FullName -Target $Target -Label "skill $($_.Name)"
    $Installed += [pscustomobject]@{
      name = $_.Name
      path = $Target
    }
  }
}

if ($PreviousManifest -and $PreviousManifest.skills -and
    [string]$PreviousManifest.skills_root -and
    (Resolve-InstallPath ([string]$PreviousManifest.skills_root)) -eq $SkillsRoot) {
  $CurrentTargets = @($Installed | ForEach-Object { Resolve-InstallPath ([string]$_.path) })
  foreach ($PreviousSkill in $PreviousManifest.skills) {
    $PreviousPath = [string]$PreviousSkill.path
    if (-not $PreviousPath) {
      continue
    }
    $ResolvedPreviousPath = Resolve-InstallPath $PreviousPath
    if ($CurrentTargets -notcontains $ResolvedPreviousPath) {
      if (-not (Test-IsUnderRoot -Path $ResolvedPreviousPath -Root $SkillsRoot)) {
        throw "Refusing to manage obsolete skill outside SkillsRoot. Target=$ResolvedPreviousPath SkillsRoot=$SkillsRoot"
      }
      if (Test-Path $ResolvedPreviousPath) {
        $Backup = "$ResolvedPreviousPath.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        if ($DryRun) {
          Write-Output "DRY RUN: would back up obsolete pack-managed skill to $Backup"
          Write-Output "DRY RUN: would remove obsolete pack-managed skill at $ResolvedPreviousPath"
        } elseif ($PSCmdlet.ShouldProcess($ResolvedPreviousPath, "Remove obsolete pack-managed skill")) {
          Copy-Item -LiteralPath $ResolvedPreviousPath -Destination $Backup -Recurse -Force
          Remove-Item -LiteralPath $ResolvedPreviousPath -Recurse -Force
          Write-Output "Removed obsolete pack-managed skill: $ResolvedPreviousPath"
        }
      }
    }
  }
} elseif ($PreviousManifest -and $PreviousManifest.skills) {
  Write-Output "Previous install used a different SkillsRoot. Obsolete skills there were left unchanged."
}

$SupportItems = if ($PackManifest -and $PackManifest.support_items) {
  @($PackManifest.support_items)
} else {
  @(
    "templates",
    "docs",
    "codex-capabilities",
    "external-skills",
    "hooks",
    "patches",
    "scripts",
    ".codex",
    ".github",
    "tests",
    "pack.manifest.json",
    "THIRD_PARTY_SKILLS.md",
    "README.md",
    "CHANGELOG.md",
    "LICENSE.md",
    "COMMERCIAL.md",
    "NOTICE.md"
  )
}

foreach ($Item in $SupportItems) {
  $PortableItem = Convert-PackPath ([string]$Item)
  $Source = Join-Path $RepoRoot $PortableItem
  if (Test-Path $Source) {
    $Target = Join-Path $ManifestDir $PortableItem
    if ($PSCmdlet.ShouldProcess($Target, "Install support item $PortableItem")) {
      if ((Get-Item -LiteralPath $Source).PSIsContainer) {
        Invoke-CopyDirectoryClean -Source $Source -Target $Target -Label "support item $PortableItem"
      } else {
        Invoke-CopyFileClean -Source $Source -Target $Target -Label "support item $PortableItem"
      }
    }
  }
}

if ((Resolve-InstallPath $LegacyManifestDir) -ne (Resolve-InstallPath $ManifestDir) -and
    (Test-Path $LegacyManifestDir)) {
  if (-not (Test-IsUnderRoot -Path $LegacyManifestDir -Root $CodexHome)) {
    throw "Refusing to remove legacy support files outside CodexHome. Target=$LegacyManifestDir CodexHome=$CodexHome"
  }
  if ($DryRun) {
    Write-Output "DRY RUN: would remove legacy support files at $LegacyManifestDir"
  } elseif ($PSCmdlet.ShouldProcess($LegacyManifestDir, "Remove legacy support files")) {
    Remove-Item -LiteralPath $LegacyManifestDir -Recurse -Force
    Write-Output "Removed legacy support files: $LegacyManifestDir"
  }
}

$GlobalAgents = Join-Path $CodexHome "AGENTS.md"
$GlobalAgentsUpdated = $false
if ($InstallGlobalAgents) {
  $PackAgents = Join-Path $RepoRoot "AGENTS.md"
  $Start = "# BEGIN CODEX CODING OS"
  $End = "# END CODEX CODING OS"
  $PackText = Get-Content -Raw -LiteralPath $PackAgents
  $Block = "$Start`r`n$PackText`r`n$End"

  Write-Output "Global AGENTS.md update requested."
  Write-Output "Target: $GlobalAgents"
  Write-Output "Inserted block markers: $Start / $End"

  if ($DryRun) {
    Write-Output "DRY RUN: would write this global AGENTS block:"
    Write-Output $Block
  } elseif ($PSCmdlet.ShouldProcess($GlobalAgents, "Update global AGENTS.md")) {
    if (Test-Path $GlobalAgents) {
      $Existing = Get-Content -Raw -LiteralPath $GlobalAgents
      $Backup = "$GlobalAgents.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
      Copy-Item -LiteralPath $GlobalAgents -Destination $Backup -Force
      $Updated = Get-UpdatedAgentsText -Existing $Existing -Block $Block
      Set-Content -LiteralPath $GlobalAgents -Value $Updated -Encoding UTF8
      Write-Output "Updated global AGENTS.md. Backup: $Backup"
    } else {
      Set-Content -LiteralPath $GlobalAgents -Value ($Block + "`r`n") -Encoding UTF8
      Write-Output "Created global AGENTS.md."
    }
    $GlobalAgentsUpdated = $true
  }
}

$Manifest = [pscustomobject]@{
  package = "codex-coding-os"
  installed_at = (Get-Date -Format o)
  repo_root = $RepoRoot
  skills_root = $SkillsRoot
  codex_home = $CodexHome
  support_root = $ManifestDir
  installed_global_agents = [bool]$InstallGlobalAgents
  global_agents_path = $GlobalAgents
  installed_external_skills = [bool]$InstallExternalSkills
  allowed_unpinned_external_skills = [bool]$AllowUnpinnedExternalSkills
  skills = $Installed
}

if (-not $DryRun) {
  $Manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ManifestJsonPath -Encoding UTF8

  $Lines = @()
  $Lines += "ManifestVersion=2"
  $Lines += "Package=codex-coding-os"
  $Lines += "InstalledAt=$($Manifest.installed_at)"
  $Lines += "RepoRoot=$RepoRoot"
  $Lines += "SkillsRoot=$SkillsRoot"
  $Lines += "CodexHome=$CodexHome"
  $Lines += "SupportRoot=$ManifestDir"
  $Lines += "InstalledGlobalAgents=$InstallGlobalAgents"
  $Lines += "GlobalAgentsPath=$GlobalAgents"
  $Lines += "InstalledExternalSkills=$InstallExternalSkills"
  $Lines += "AllowedUnpinnedExternalSkills=$AllowUnpinnedExternalSkills"
  $Lines += ($Installed | ForEach-Object { "SkillPath=$($_.path)" })
  Set-Content -LiteralPath $ManifestTextPath -Value $Lines -Encoding UTF8
} else {
  Write-Output "DRY RUN: would write install manifests:"
  Write-Output " - $ManifestJsonPath"
  Write-Output " - $ManifestTextPath"
}

if ($InstallExternalSkills) {
  if ($DryRun) {
    Write-Output "DRY RUN: would install optional external skills and apply overlays."
  } else {
    $ExternalArgs = @(
      "-Install", "forrestchang-andrej-karpathy-skills",
      "-ApplyOverlays",
      "-TargetSkillsRoot", $SkillsRoot
    )
    if ($AllowUnpinnedExternalSkills) {
      $ExternalArgs += "-AllowUnpinned"
    }
    & (Join-Path $RepoRoot "scripts\install-external-skills.ps1") @ExternalArgs
  }
}

Write-Output "Installed skills:"
$Installed | ForEach-Object { Write-Output " - $($_.path)" }
if ($InstallGlobalAgents -and -not $GlobalAgentsUpdated -and -not $DryRun) {
  Write-Output "Global AGENTS.md was not changed."
}
Write-Output "Support files copied to: $ManifestDir"
Write-Output "Restart Codex, then use templates\first-codex-prompt.md from this repo or $ManifestDir."
