param(
  [string]$OutputPath
)

$ErrorActionPreference = "Stop"

function Test-IsExcludedName {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string[]]$ExcludedNames
  )
  $ExcludedNames -contains $Name
}

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Parent = Split-Path -Parent $RepoRoot
$ManifestPath = Join-Path $RepoRoot "pack.manifest.json"
$Validate = Join-Path $RepoRoot "scripts\validate-pack.ps1"

if (-not $OutputPath) {
  $OutputPath = Join-Path $Parent "codex-coding-os-starter.zip"
}

& $Validate

if (-not $?) {
  throw "Validation failed. Package was not created."
}

$Manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
$ExcludedNames = @(".git", ".external-sources", ".release-exclusions.local.txt", ".private-terms.local.txt")
if ($Manifest.release_safety.excluded_paths) {
  $ExcludedNames += @($Manifest.release_safety.excluded_paths)
}
$ExcludedNames = $ExcludedNames | Select-Object -Unique

if (Test-Path $OutputPath) {
  Remove-Item -LiteralPath $OutputPath -Force
}

$GitCommand = Get-Command git -ErrorAction SilentlyContinue
$GitRoot = Join-Path $RepoRoot ".git"
if ($GitCommand -and (Test-Path $GitRoot)) {
  $GitStatus = @(& git -C $RepoRoot status --porcelain --untracked-files=no)
  if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect Git working tree before packaging."
  }
  if ($GitStatus.Count -gt 0) {
    throw "Tracked Git files must match HEAD before packaging. Commit or revert tracked changes so the archive matches a reviewed revision."
  }

  & git -C $RepoRoot archive --format=zip --output=$OutputPath HEAD
  if ($LASTEXITCODE -ne 0) {
    throw "git archive failed."
  }
} else {
  $PackageItems = Get-ChildItem -Path $RepoRoot -Force | Where-Object {
    -not (Test-IsExcludedName -Name $_.Name -ExcludedNames $ExcludedNames)
  }

  Compress-Archive -Path ($PackageItems | Select-Object -ExpandProperty FullName) -DestinationPath $OutputPath -Force
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$ForbiddenExtensions = @($Manifest.release_safety.forbidden_file_extensions)
$Zip = [System.IO.Compression.ZipFile]::OpenRead($OutputPath)
try {
  $BadEntries = @()
  foreach ($Entry in $Zip.Entries) {
    $EntryName = $Entry.FullName
    foreach ($Excluded in $ExcludedNames) {
      if ($EntryName -match "(^|/)$([regex]::Escape($Excluded))(/|$)") {
        $BadEntries += "Excluded path found in archive: $EntryName"
      }
    }

    $Ext = [System.IO.Path]::GetExtension($EntryName).ToLowerInvariant()
    if ($ForbiddenExtensions -contains $Ext) {
      $BadEntries += "Forbidden extension found in archive: $EntryName"
    }
  }

  if ($BadEntries.Count -gt 0) {
    $BadEntries | ForEach-Object { Write-Output $_ }
    throw "Package archive inspection failed."
  }
} finally {
  $Zip.Dispose()
}

Write-Output "Packaged: $OutputPath"
