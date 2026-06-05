$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$TestRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("codex-coding-os-smoke-" + [guid]::NewGuid().ToString("N"))
$SkillsRoot = Join-Path $TestRoot "skills"
$CodexHome = Join-Path $TestRoot "codex-home"
$InstallScript = Join-Path $RepoRoot "scripts\install.ps1"
$UninstallScript = Join-Path $RepoRoot "scripts\uninstall.ps1"

try {
  New-Item -ItemType Directory -Force -Path $TestRoot | Out-Null

  & $InstallScript -SkillsRoot $SkillsRoot -CodexHome $CodexHome -InstallGlobalAgents -Confirm:$false
  if (-not $?) { throw "Install failed." }

  $ManifestPath = Join-Path $CodexHome "coding-os-starter\install-manifest.json"
  if (-not (Test-Path $ManifestPath)) {
    throw "Install manifest was not written."
  }

  $MasterSkill = Join-Path $SkillsRoot "codex-coding-os-master\SKILL.md"
  if (-not (Test-Path $MasterSkill)) {
    throw "Master skill was not installed."
  }

  $GlobalAgents = Join-Path $CodexHome "AGENTS.md"
  $GlobalText = Get-Content -Raw -LiteralPath $GlobalAgents
  if ($GlobalText -notmatch "# BEGIN CODEX CODING OS STARTER") {
    throw "Global AGENTS block was not installed into the temp Codex home."
  }

  $StaleFile = Join-Path $SkillsRoot "ai-coding-discipline\STALE.txt"
  Set-Content -LiteralPath $StaleFile -Value "stale file from previous install" -Encoding UTF8

  & $InstallScript -SkillsRoot $SkillsRoot -CodexHome $CodexHome -Confirm:$false
  if (-not $?) { throw "Reinstall failed." }

  if (Test-Path $StaleFile) {
    throw "Reinstall left a stale file in an existing skill folder."
  }

  & $UninstallScript -SkillsRoot $SkillsRoot -CodexHome $CodexHome -Confirm:$false
  if (-not $?) { throw "Uninstall failed." }

  if (Test-Path $MasterSkill) {
    throw "Uninstall did not remove installed skills from the custom SkillsRoot."
  }

  if (Test-Path (Join-Path $CodexHome "coding-os-starter")) {
    throw "Uninstall did not remove support files from the custom CodexHome."
  }

  if (Test-Path $GlobalAgents) {
    $AfterUninstall = Get-Content -Raw -LiteralPath $GlobalAgents
    if ($AfterUninstall -match "# BEGIN CODEX CODING OS STARTER") {
      throw "Uninstall did not remove the global AGENTS block from the custom CodexHome."
    }
  }

  Write-Output "Install/uninstall smoke test passed."
} finally {
  if (Test-Path $TestRoot) {
    Remove-Item -LiteralPath $TestRoot -Recurse -Force
  }
}
