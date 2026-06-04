$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Parent = Split-Path -Parent $RepoRoot
$ZipPath = Join-Path $Parent "codex-coding-os-starter.zip"
$Validate = Join-Path $RepoRoot "scripts\validate-pack.ps1"

& $Validate

if (Test-Path $ZipPath) {
  Remove-Item -LiteralPath $ZipPath -Force
}

Compress-Archive -Path (Join-Path $RepoRoot "*") -DestinationPath $ZipPath -Force

Write-Output "Packaged: $ZipPath"

