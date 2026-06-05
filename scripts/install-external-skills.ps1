param(
  [string]$Install,
  [switch]$ApplyOverlays,
  [switch]$List,
  [string]$TargetSkillsRoot = "$HOME\.agents\skills"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ManifestPath = Join-Path $RepoRoot "external-skills\manifest.json"
$ExternalRoot = Join-Path $RepoRoot ".external-sources"

if (-not (Test-Path $ManifestPath)) {
  throw "External skills manifest not found: $ManifestPath"
}

$Manifest = Get-Content -Raw -Path $ManifestPath | ConvertFrom-Json

if ($List) {
  $Manifest.sources | Select-Object id, repo, treatment, default_action
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
if (-not (Test-Path $ClonePath)) {
  git clone --depth 1 $Source.repo $ClonePath
} else {
  git -C $ClonePath pull --ff-only
}

$Installed = @()
foreach ($RelPath in $Source.expected_skill_paths) {
  $Candidate = Join-Path $ClonePath $RelPath
  if (Test-Path $Candidate) {
    $Target = Join-Path $TargetSkillsRoot (Split-Path -Leaf $Candidate)
    Copy-Item -LiteralPath $Candidate -Destination $Target -Recurse -Force
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

