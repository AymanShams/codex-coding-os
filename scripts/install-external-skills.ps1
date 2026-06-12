param(
  [string]$Install,
  [switch]$ApplyOverlays,
  [switch]$List,
  [string]$TargetSkillsRoot = "$HOME\.agents\skills",
  [switch]$AllowUnpinned
)

$ErrorActionPreference = "Stop"

function Resolve-InstallPath {
  param([Parameter(Mandatory = $true)][string]$Path)
  $Expanded = [Environment]::ExpandEnvironmentVariables($Path)
  $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Expanded)
}

function Require-Command {
  param([Parameter(Mandatory = $true)][string]$Name)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "$Name is required for external skill installation."
  }
}

function Copy-DirectoryClean {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Target
  )
  if (Test-Path $Target) {
    $Backup = "$Target.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $Target -Destination $Backup -Recurse -Force
    Remove-Item -LiteralPath $Target -Recurse -Force
  }
  Copy-Item -LiteralPath $Source -Destination $Target -Recurse -Force
}

function Sync-ExternalRepo {
  param(
    [Parameter(Mandatory = $true)]$Source,
    [Parameter(Mandatory = $true)][string]$ClonePath
  )

  $PinnedCommit = [string]$Source.pinned_commit
  if ([string]::IsNullOrWhiteSpace($PinnedCommit)) {
    if (-not $AllowUnpinned) {
      throw "External source '$($Source.id)' is installable but not pinned. Add pinned_commit to external-skills/manifest.json or rerun with -AllowUnpinned after reviewing upstream."
    }

    if (-not (Test-Path $ClonePath)) {
      git clone --depth 1 $Source.repo $ClonePath
    } else {
      git -C $ClonePath pull --ff-only
    }
    return
  }

  if (-not (Test-Path $ClonePath)) {
    git clone --no-checkout $Source.repo $ClonePath
  }
  git -C $ClonePath fetch --depth 1 origin $PinnedCommit
  git -C $ClonePath checkout --detach $PinnedCommit
  $ResolvedCommit = (git -C $ClonePath rev-parse HEAD).Trim()
  if ($ResolvedCommit -ne $PinnedCommit) {
    throw "External source '$($Source.id)' resolved to $ResolvedCommit instead of pinned commit $PinnedCommit."
  }
}

Require-Command -Name "git"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ManifestPath = Join-Path $RepoRoot "external-skills\manifest.json"
$ExternalRoot = Join-Path $RepoRoot ".external-sources"
$TargetSkillsRoot = Resolve-InstallPath $TargetSkillsRoot

if (-not (Test-Path $ManifestPath)) {
  throw "External skills manifest not found: $ManifestPath"
}

$Manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json

if ($List) {
  $Manifest.sources | Select-Object id, repo, treatment, default_action, pin_status, pinned_commit, reviewed_at, license
  exit 0
}

if (-not $Install) {
  Write-Output "No external skill selected. Use -List to view options."
  exit 0
}

$Source = $Manifest.sources | Where-Object { $_.id -eq $Install } | Select-Object -First 1
if (-not $Source) {
  throw "Unknown external skill source: $Install"
}

if ($Source.treatment -notmatch "optional-install") {
  Write-Output "This source is marked $($Source.treatment). It will not be installed automatically."
  Write-Output "Repo: $($Source.repo)"
  exit 0
}

New-Item -ItemType Directory -Force -Path $ExternalRoot | Out-Null
New-Item -ItemType Directory -Force -Path $TargetSkillsRoot | Out-Null

$ClonePath = Join-Path $ExternalRoot $Source.id
Sync-ExternalRepo -Source $Source -ClonePath $ClonePath

$Installed = @()
foreach ($RelPath in $Source.expected_skill_paths) {
  $Candidate = Join-Path $ClonePath $RelPath
  if (Test-Path $Candidate) {
    $Target = Join-Path $TargetSkillsRoot (Split-Path -Leaf $Candidate)
    Copy-DirectoryClean -Source $Candidate -Target $Target
    $Installed += $Target
  }
}

if ($Installed.Count -eq 0) {
  Write-Output "No expected skill paths found after clone. Inspect: $ClonePath"
} else {
  Write-Output "Installed external skills:"
  $Installed | ForEach-Object { Write-Output " - $_" }
}

if ($ApplyOverlays) {
  & (Join-Path $RepoRoot "scripts\apply-external-skill-overlays.ps1") -TargetSkillsRoot $TargetSkillsRoot
}
