$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Engine = Join-Path $RepoRoot "scripts\install_transaction.py"
$Bundle = Get-Content -Raw -LiteralPath (Join-Path $RepoRoot "install-bundle.manifest.json") | ConvertFrom-Json
$BundleHash = [string]$Bundle.aggregate_sha256

function Invoke-Engine {
  param([string[]]$Arguments)
  & python -B $Engine --json @Arguments | Out-Host
  $Code = $LASTEXITCODE
  return $Code
}

function New-SyntheticRoots {
  $Root = Join-Path ([System.IO.Path]::GetTempPath()) ("ccos-hard-fault-" + [guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Force -Path $Root | Out-Null
  return [pscustomobject]@{
    Root = $Root
    Skills = Join-Path $Root "skills"
    Codex = Join-Path $Root "codex-home"
  }
}

$Scenarios = @(
  @{ Operation = "install"; Point = "PROMOTION:middle"; ExpectedExit = 86 },
  @{ Operation = "install"; Point = "CURRENT_POINTER_COMMITTED"; ExpectedExit = 86 },
  @{ Operation = "uninstall"; Point = "PROMOTION:middle"; ExpectedExit = 86 }
)

foreach ($Scenario in $Scenarios) {
  $Roots = New-SyntheticRoots
  try {
    $InstallArgs = @(
      "install", "--source-root", $RepoRoot,
      "--skills-root", $Roots.Skills,
      "--codex-home", $Roots.Codex,
      "--expected-bundle-sha256", $BundleHash,
      "--archive-mode"
    )
    if ($Scenario.Operation -eq "uninstall") {
      $Code = Invoke-Engine -Arguments $InstallArgs
      if ($Code -ne 0) { throw "Fault precondition install failed." }
    }
    $env:CCOS_INSTALL_TEST_MODE = "1"
    $env:CCOS_INSTALL_TEST_FAIL_AFTER = $Scenario.Point
    $env:CCOS_INSTALL_TEST_HARD_CRASH = "1"
    if ($Scenario.Operation -eq "install") {
      $Code = Invoke-Engine -Arguments $InstallArgs
    } else {
      $Code = Invoke-Engine -Arguments @("uninstall", "--skills-root", $Roots.Skills, "--codex-home", $Roots.Codex)
    }
    if ($Code -ne $Scenario.ExpectedExit) { throw "Expected hard crash exit $($Scenario.ExpectedExit), got $Code." }
    Remove-Item Env:CCOS_INSTALL_TEST_MODE, Env:CCOS_INSTALL_TEST_FAIL_AFTER, Env:CCOS_INSTALL_TEST_HARD_CRASH -ErrorAction SilentlyContinue
    if ($Scenario.Operation -eq "install") {
      $Code = Invoke-Engine -Arguments $InstallArgs
      if ($Code -ne 0) { throw "Install recovery invocation failed." }
      $Current = Get-Content -Raw -LiteralPath (Join-Path $Roots.Codex ".coding-os-install\current.json") | ConvertFrom-Json
      if ($Current.status -ne "committed") { throw "Recovered install did not commit." }
    } else {
      $Code = Invoke-Engine -Arguments @("uninstall", "--skills-root", $Roots.Skills, "--codex-home", $Roots.Codex)
      if ($Code -ne 0) { throw "Uninstall recovery invocation failed." }
      $Current = Get-Content -Raw -LiteralPath (Join-Path $Roots.Codex ".coding-os-install\current.json") | ConvertFrom-Json
      if ($Current.status -ne "uninstalled") { throw "Recovered uninstall did not commit." }
    }
  } finally {
    Remove-Item Env:CCOS_INSTALL_TEST_MODE, Env:CCOS_INSTALL_TEST_FAIL_AFTER, Env:CCOS_INSTALL_TEST_HARD_CRASH -ErrorAction SilentlyContinue
    if (Test-Path $Roots.Root) { Remove-Item -LiteralPath $Roots.Root -Recurse -Force }
  }
}

Write-Output "Transactional hard-crash recovery tests passed."
