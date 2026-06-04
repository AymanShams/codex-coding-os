$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SkillSourceRoot = Join-Path $RepoRoot ".agents\skills"
$UserSkillRoot = Join-Path $HOME ".agents\skills"
$Markers = @{
  Start = "# BEGIN CODEX CODING OS STARTER"
  End = "# END CODEX CODING OS STARTER"
}

if (Test-Path $SkillSourceRoot) {
  Get-ChildItem -Path $SkillSourceRoot -Directory | ForEach-Object {
    $Target = Join-Path $UserSkillRoot $_.Name
    if (Test-Path $Target) {
      Remove-Item -LiteralPath $Target -Recurse -Force
      Write-Output "Removed skill: $($_.Name)"
    }
  }
}

$AgentsPath = Join-Path $HOME ".codex\AGENTS.md"
if (Test-Path $AgentsPath) {
  $Existing = Get-Content -Raw -Path $AgentsPath
  $Pattern = "(?s)\r?\n?$([regex]::Escape($Markers.Start)).*?$([regex]::Escape($Markers.End))\r?\n?"
  if ($Existing -match $Pattern) {
    $Backup = "$AgentsPath.bak.$(Get-Date -Format yyyyMMddHHmmss)"
    Copy-Item -Path $AgentsPath -Destination $Backup
    $Updated = [regex]::Replace($Existing, $Pattern, "")
    Set-Content -Path $AgentsPath -Value $Updated -Encoding UTF8
    Write-Output "Removed global AGENTS block. Backup: $Backup"
  }
}

Write-Output "Uninstall complete."
