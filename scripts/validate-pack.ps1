param(
  [switch]$RequireExternalScanners
)

$ErrorActionPreference = "Stop"

function Convert-PackPath {
  param([Parameter(Mandatory = $true)][string]$Path)
  $Path -replace "/", [System.IO.Path]::DirectorySeparatorChar
}

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ManifestPath = Join-Path $RepoRoot "pack.manifest.json"
$Errors = @()

if (-not (Test-Path $ManifestPath)) {
  Write-Output "Validation failed:"
  Write-Output " - Missing pack.manifest.json"
  exit 1
}

try {
  $Manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
} catch {
  Write-Output "Validation failed:"
  Write-Output " - pack.manifest.json is not valid JSON: $($_.Exception.Message)"
  exit 1
}

if ([string]$Manifest.version -notmatch '^\d+\.\d+\.\d+([\-+][0-9A-Za-z.-]+)?$') {
  $Errors += "pack.manifest.json version must be valid SemVer: $($Manifest.version)"
}

$SchemaReference = [string]$Manifest.'$schema'
if ($SchemaReference -ne "pack.schema.json") {
  $Errors += "pack.manifest.json must reference pack.schema.json in the `$schema field."
}

$SchemaPath = Join-Path $RepoRoot "pack.schema.json"
if (-not (Test-Path $SchemaPath)) {
  $Errors += "Missing manifest schema reference: pack.schema.json"
} else {
  try {
    $null = Get-Content -Raw -LiteralPath $SchemaPath | ConvertFrom-Json
  } catch {
    $Errors += "pack.schema.json is not valid JSON: $($_.Exception.Message)"
  }
}

$SchemaValidationHelper = Join-Path $RepoRoot "scripts\json-schema-validation.ps1"
if (-not (Test-Path -LiteralPath $SchemaValidationHelper -PathType Leaf)) {
  $Errors += "Missing JSON Schema validation helper."
} else {
  . $SchemaValidationHelper
  $SchemaValidationErrors = @(
    Get-JsonSchemaValidationErrors -JsonPath $ManifestPath -SchemaPath $SchemaPath
  )
  foreach ($SchemaValidationError in $SchemaValidationErrors) {
    $Errors += "pack.manifest.json schema validation failed: $SchemaValidationError"
  }

  $RoutingContracts = @(
    @{
      Json = Join-Path $RepoRoot "capability-routing\routing-policy.yaml"
      Schema = Join-Path $RepoRoot "capability-routing\routing-policy.schema.json"
      Name = "routing policy"
    },
    @{
      Json = Join-Path $RepoRoot "capability-routing\project-scope-map.example.json"
      Schema = Join-Path $RepoRoot "capability-routing\project-scope-map.schema.json"
      Name = "project scope map example"
    }
  )
  foreach ($Contract in $RoutingContracts) {
    if (-not (Test-Path -LiteralPath $Contract.Json -PathType Leaf) -or
        -not (Test-Path -LiteralPath $Contract.Schema -PathType Leaf)) {
      $Errors += "Missing capability routing contract: $($Contract.Name)"
      continue
    }
    foreach ($ContractError in @(
      Get-JsonSchemaValidationErrors -JsonPath $Contract.Json -SchemaPath $Contract.Schema
    )) {
      $Errors += "$($Contract.Name) schema validation failed: $ContractError"
    }
  }
}

$VersionFile = Join-Path $RepoRoot "VERSION"
if (Test-Path $VersionFile) {
  $Errors += "VERSION file is not allowed. pack.manifest.json#version is the sole package release version."
}

$ChangelogPath = Join-Path $RepoRoot "CHANGELOG.md"
if (Test-Path $ChangelogPath) {
  $ChangelogText = Get-Content -Raw -LiteralPath $ChangelogPath
  $EscapedVersion = [regex]::Escape([string]$Manifest.version)
  if ($ChangelogText -notmatch "(?m)^## \[$EscapedVersion\]") {
    $Errors += "CHANGELOG.md must contain an entry for package version $($Manifest.version)."
  }
}

foreach ($Path in $Manifest.required_files) {
  $Full = Join-Path $RepoRoot (Convert-PackPath $Path)
  if (-not (Test-Path $Full)) {
    $Errors += "Missing required file: $Path"
  }
}

foreach ($Property in $Manifest.source_of_truth.PSObject.Properties) {
  $SourcePath = ([string]$Property.Value).Split("#")[0]
  $Full = Join-Path $RepoRoot (Convert-PackPath $SourcePath)
  if (-not (Test-Path $Full)) {
    $Errors += "Missing source-of-truth path '$($Property.Name)': $SourcePath"
  }
}

$DocumentationContractValidator = Join-Path $RepoRoot "scripts\validate_documentation_contracts.py"
if (-not (Test-Path $DocumentationContractValidator -PathType Leaf)) {
  $Errors += "Missing documentation contract validator."
} else {
  & python -B $DocumentationContractValidator --repo-root $RepoRoot
  if ($LASTEXITCODE -ne 0) {
    $Errors += "Documentation contract validation failed."
  }
}

$CapabilityContractTests = @(
  "tests.test_repository_capability_router",
  "tests.test_capability_manifest_recovery",
  "tests.test_capability_manifest_builder",
  "tests.test_catalogue_router_wrapper",
  "tests.test_security_capability_routing",
  "tests.test_local_security_skill_parity",
  "tests.test_plugin_manifest_boundaries"
)
& python -B -m unittest @CapabilityContractTests
if ($LASTEXITCODE -ne 0) {
  $Errors += "Canonical router and security capability contract tests failed."
}

foreach ($Path in $Manifest.support_items) {
  $Full = Join-Path $RepoRoot (Convert-PackPath ([string]$Path))
  if (-not (Test-Path $Full)) {
    $Errors += "Missing support item: $Path"
  }
}

$RequiredCampaignRuntime = @(
  "scripts/agent/case_state.py",
  "scripts/agent/campaign_engine/__init__.py",
  "scripts/agent/campaign_engine/__main__.py",
  "scripts/agent/campaign_engine/model.py",
  "scripts/agent/campaign_engine/reducer.py",
  "scripts/agent/campaign_engine/store.py",
  "scripts/agent/campaign_engine/admission.py",
  "scripts/agent/campaign_engine/supervisor.py",
  "scripts/agent/campaign_engine/host.py",
  "scripts/agent/campaign_engine/evidence.py",
  "scripts/agent/campaign_engine/effects.py",
  "scripts/agent/campaign_engine/legacy.py",
  "scripts/agent/campaign_engine/cli.py",
  "hooks/campaign-engine/campaign_hook.py"
)
foreach ($Path in $RequiredCampaignRuntime) {
  if (@($Manifest.installation.runtime_files) -notcontains $Path) {
    $Errors += "Missing mandatory campaign runtime declaration: $Path"
  }
}

$SkillRoot = Join-Path $RepoRoot ".agents\skills"
if (-not (Test-Path $SkillRoot)) {
  $Errors += "Missing bundled skill root: .agents/skills"
} else {
  foreach ($Skill in $Manifest.bundled_skills) {
    $SkillName = [string]$Skill.name
    $SkillFile = Join-Path $SkillRoot "$SkillName\SKILL.md"
    if (-not (Test-Path $SkillFile)) {
      $Errors += "Missing bundled full skill: $SkillName"
    }
  }

  Get-ChildItem -Path $SkillRoot -Directory | ForEach-Object {
    $SkillFile = Join-Path $_.FullName "SKILL.md"
    if (-not (Test-Path $SkillFile)) {
      $Errors += "Missing SKILL.md in $($_.FullName)"
    } else {
      $Text = Get-Content -Raw -LiteralPath $SkillFile
      if ($Text -notmatch "(?s)^---.*name:.*description:.*---") {
        $Errors += "Missing frontmatter fields in $SkillFile"
      }
    }
  }
}

$ExternalManifestPath = Join-Path $RepoRoot "external-skills\manifest.json"
if (Test-Path $ExternalManifestPath) {
  try {
    $ExternalManifest = Get-Content -Raw -LiteralPath $ExternalManifestPath | ConvertFrom-Json
    foreach ($Source in $ExternalManifest.sources) {
      if (-not $Source.id) { $Errors += "External source missing id." }
      if (-not $Source.repo) { $Errors += "External source missing repo: $($Source.id)" }
      if (-not $Source.treatment) { $Errors += "External source missing treatment: $($Source.id)" }
      if (-not $Source.license) { $Errors += "External source missing license metadata: $($Source.id)" }
      if (-not $Source.reviewed_at) { $Errors += "External source missing reviewed_at: $($Source.id)" }
      if ($null -eq $Source.pinned_commit) { $Errors += "External source missing pinned_commit field: $($Source.id)" }
      if ($null -eq $Source.sha256) { $Errors += "External source missing sha256 field: $($Source.id)" }
      if (-not $Source.pin_status) { $Errors += "External source missing pin_status: $($Source.id)" }
      if ($Source.treatment -match "optional-install" -and [string]::IsNullOrWhiteSpace([string]$Source.pinned_commit)) {
        if ($Source.pin_status -ne "required-before-repeatable-install") {
          $Errors += "Installable external source without pinned_commit must set pin_status=required-before-repeatable-install: $($Source.id)"
        }
      }
      if ($Source.treatment -match "optional-install" -and -not [string]::IsNullOrWhiteSpace([string]$Source.pinned_commit)) {
        if ($Source.pin_status -ne "pinned-reviewed") {
          $Errors += "Pinned installable external source must set pin_status=pinned-reviewed: $($Source.id)"
        }
        if ([string]$Source.pinned_commit -notmatch '^[0-9a-fA-F]{40}$') {
          $Errors += "Pinned installable external source must use a full 40-character commit SHA: $($Source.id)"
        }
        if ([string]$Source.integrity_control -ne "git-commit-pin") {
          $Errors += "Pinned Git installable source must set integrity_control=git-commit-pin: $($Source.id)"
        }
        if ([string]$Source.sha256 -ne "not-applicable-git-commit-pin" -and
            [string]$Source.sha256 -notmatch '^[0-9a-fA-F]{64}$') {
          $Errors += "Pinned Git installable source sha256 must be a verified archive hash or not-applicable-git-commit-pin: $($Source.id)"
        }
      }
      foreach ($OverlayPath in @($Source.overlay_paths)) {
        if ([string]::IsNullOrWhiteSpace([string]$OverlayPath)) {
          continue
        }
        $FullOverlayPath = Join-Path $RepoRoot (Convert-PackPath ([string]$OverlayPath))
        if (-not (Test-Path $FullOverlayPath)) {
          $Errors += "External source overlay path does not exist: $OverlayPath"
        }
      }
      if ($Source.treatment -eq "reference-only" -and $Source.pin_status -ne "reference-only-not-installed") {
        $Errors += "Reference-only external source must set pin_status=reference-only-not-installed: $($Source.id)"
      }
    }
  } catch {
    $Errors += "external-skills/manifest.json is not valid JSON: $($_.Exception.Message)"
  }
}

if ($Errors.Count -eq 0) {
  $TransactionEngine = Join-Path $RepoRoot "scripts\install_transaction.py"
  $BundleManifest = Join-Path $RepoRoot "install-bundle.manifest.json"
  if (-not (Test-Path $TransactionEngine -PathType Leaf)) {
    $Errors += "Missing transactional install engine."
  } elseif (-not (Test-Path $BundleManifest -PathType Leaf)) {
    $Errors += "Missing install-bundle.manifest.json."
  } else {
    & python -B $TransactionEngine --json verify-bundle --repo-root $RepoRoot
    if ($LASTEXITCODE -ne 0) {
      $Errors += "Transactional install bundle verification failed."
    }
  }
}

if ($Errors.Count -eq 0) {
  $CampaignCliPath = Join-Path $RepoRoot "scripts\agent\campaign_engine\cli.py"
  $CampaignReducerPath = Join-Path $RepoRoot "scripts\agent\campaign_engine\reducer.py"
  $LegacyCliPath = Join-Path $RepoRoot "scripts\agent\case_state.py"
  $CampaignFormalPath = Join-Path $RepoRoot "formal\Campaign.tla"
  $CampaignConfigPath = Join-Path $RepoRoot "formal\Campaign.cfg"
  $IncidentIndexPath = Join-Path $RepoRoot "tests\fixtures\incidents\index.json"

  foreach ($Path in @($CampaignCliPath, $CampaignReducerPath, $LegacyCliPath, $CampaignFormalPath, $CampaignConfigPath, $IncidentIndexPath)) {
    if (-not (Test-Path $Path -PathType Leaf)) {
      $Errors += "Missing campaign source contract: $Path"
    }
  }

  if ($Errors.Count -eq 0) {
    $CampaignCli = Get-Content -Raw -LiteralPath $CampaignCliPath
    $CampaignReducer = Get-Content -Raw -LiteralPath $CampaignReducerPath
    $LegacyCli = Get-Content -Raw -LiteralPath $LegacyCliPath
    $CampaignFormal = Get-Content -Raw -LiteralPath $CampaignFormalPath
    foreach ($Command in @("admit", "approve", "run", "status", "cancel", "reconcile", "doctor", "legacy")) {
      if ($CampaignCli -notlike "*sub.add_parser(`"$Command`")*") {
        $Errors += "Campaign CLI is missing public command: $Command"
      }
    }
    if ($CampaignReducer -notmatch 'def\s+reduce\(') {
      $Errors += "Campaign reducer has no reduce function."
    }
    foreach ($Marker in @("LEGACY_ENGINE_RETIRED", "campaign_engine.cli legacy inspect", "return 78")) {
      if ($LegacyCli -notlike "*$Marker*") {
        $Errors += "Retired case CLI is missing deterministic retirement marker: $Marker"
      }
    }
    foreach ($Marker in @("MODULE Campaign", "Spec == Init /\ [][Next]_vars", "CancelledIsTerminal")) {
      if ($CampaignFormal -notmatch [regex]::Escape($Marker)) {
        $Errors += "Campaign formal model is missing marker: $Marker"
      }
    }
    try {
      $IncidentIndex = Get-Content -Raw -LiteralPath $IncidentIndexPath | ConvertFrom-Json
      if ($IncidentIndex.schema_version -ne 1 -or @($IncidentIndex.incidents).Count -eq 0) {
        $Errors += "Campaign incident index is incomplete."
      } else {
        foreach ($Incident in $IncidentIndex.incidents) {
          foreach ($Fixture in @($Incident.historical_fixture, $Incident.opposite_fixture)) {
            if ([string]::IsNullOrWhiteSpace([string]$Fixture) -or -not (Test-Path (Join-Path $RepoRoot "tests\fixtures\incidents\$Fixture") -PathType Leaf)) {
              $Errors += "Campaign incident fixture is missing: $Fixture"
            }
          }
        }
      }
    } catch {
      $Errors += "Campaign incident index is not valid JSON: $($_.Exception.Message)"
    }
  }
}

if ($Errors.Count -eq 0) {
  $LinkScript = Join-Path $RepoRoot "scripts\validate-links.ps1"
  if (Test-Path $LinkScript) {
    & $LinkScript -RepoRoot $RepoRoot
    if (-not $?) {
      $Errors += "Internal link validation failed."
    }
  } else {
    $Errors += "Missing internal link validation script."
  }
}

if ($Errors.Count -eq 0) {
  $SafetyScript = Join-Path $RepoRoot "scripts\release-safety-scan.ps1"
  if (Test-Path $SafetyScript) {
    if ($RequireExternalScanners) {
      & $SafetyScript -RepoRoot $RepoRoot -RequireExternalScanners
    } else {
      & $SafetyScript -RepoRoot $RepoRoot
    }
    if (-not $?) {
      $Errors += "Release safety scan failed."
    }
  } else {
    $Errors += "Missing release safety scan script."
  }
}

if ($Errors.Count -gt 0) {
  Write-Output "Validation failed:"
  $Errors | ForEach-Object { Write-Output " - $_" }
  exit 1
}

Write-Output "Validation passed."
