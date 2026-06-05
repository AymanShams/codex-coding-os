$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Parent = Split-Path -Parent $RepoRoot
$ZipPath = Join-Path $Parent "codex-coding-os-starter.zip"
$Validate = Join-Path $RepoRoot "scripts\validate-pack.ps1"

& $Validate

if (Test-Path $ZipPath) {
  Remove-Item -LiteralPath $ZipPath -Force
}

$ExcludedNames = @(
  ".git",
  ".external-sources",
  ".release-exclusions.local.txt"
)

$PackageItems = Get-ChildItem -Path $RepoRoot -Force | Where-Object {
  $ExcludedNames -notcontains $_.Name
}

Compress-Archive -Path ($PackageItems | Select-Object -ExpandProperty FullName) -DestinationPath $ZipPath -Force

Write-Output "Packaged: $ZipPath"
