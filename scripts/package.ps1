param(
  [string]$OutputPath
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Parent = Split-Path -Parent $RepoRoot
$ManifestPath = Join-Path $RepoRoot "pack.manifest.json"
$Validate = Join-Path $RepoRoot "scripts\validate-pack.ps1"
$RequiredDormantRoutingFiles = @(
  "capability-routing/README.md",
  "capability-routing/provenance.json",
  "capability-routing/active-capabilities.schema.json",
  "capability-routing/authority-receipt.schema.json",
  "capability-routing/route-decision.schema.json",
  "capability-routing/routing-policy.schema.json",
  "capability-routing/routing-policy.yaml",
  "capability-routing/project-scope-map.schema.json",
  "capability-routing/project-scope-map.example.json",
  "capability-routing/builder/build_canonical_capability_manifest.ps1",
  "capability-routing/reference-runtime/_common.py",
  "capability-routing/reference-runtime/_hook_io.py",
  "capability-routing/reference-runtime/capability_config_fingerprint.py",
  "capability-routing/reference-runtime/capability_index.py",
  "capability-routing/reference-runtime/capability_index_cli.py",
  "capability-routing/reference-runtime/capability_index_session_start.py",
  "capability-routing/reference-runtime/capability_manifest_recovery.py",
  "capability-routing/reference-runtime/user_prompt_skill_router.py"
)
$RequiredRepositorySecuritySkills = @(
  "defensive-security-checklist",
  "postgres-security-best-practices",
  "security-best-practices",
  "security-ownership-map",
  "security-threat-model"
)
$ManagedPluginIds = @(
  "codex-security@openai-curated-remote",
  "supabase@openai-curated-remote",
  "neon-postgres@openai-curated-remote"
)

if (-not $OutputPath) {
  $OutputPath = Join-Path $Parent "codex-coding-os.zip"
}

& $Validate

if (-not $?) {
  throw "Validation failed. Package was not created."
}

$Manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
$BundleManifestPath = Join-Path $RepoRoot "install-bundle.manifest.json"
$BundleManifest = Get-Content -Raw -LiteralPath $BundleManifestPath | ConvertFrom-Json
if ([string]$BundleManifest.protocol -ne "CCOS-INSTALL-BUNDLE-v1" -or
    [string]$BundleManifest.package.version -ne [string]$Manifest.version) {
  throw "Install bundle manifest does not match the package release version."
}
$PluginsManifestPath = Join-Path $RepoRoot "codex-capabilities\plugins.manifest.json"
$PluginsManifest = Get-Content -Raw -LiteralPath $PluginsManifestPath | ConvertFrom-Json
$CodexManagedPluginSkillDirectories = @()
foreach ($Plugin in @($PluginsManifest.recommended_plugins)) {
  if ($ManagedPluginIds -notcontains [string]$Plugin.plugin_id) {
    continue
  }
  if ($Plugin.management -ne "third-party-codex-managed" -or $Plugin.repo_bundled -ne $false) {
    throw "Codex-managed plugin boundary metadata is invalid: $($Plugin.plugin_id)"
  }
  foreach ($SkillId in @($Plugin.managed_skills)) {
    $SkillParts = @(([string]$SkillId) -split ":", 2)
    if ($SkillParts.Count -ne 2 -or [string]::IsNullOrWhiteSpace($SkillParts[1])) {
      throw "Codex-managed plugin skill identifier is invalid: $SkillId"
    }
    $CodexManagedPluginSkillDirectories += $SkillParts[1]
  }
}
$CodexManagedPluginSkillDirectories = @($CodexManagedPluginSkillDirectories | Sort-Object -Unique)
if ($CodexManagedPluginSkillDirectories.Count -ne 17) {
  throw "Codex Security, Supabase, and Neon must declare exactly 17 Codex-managed plugin skills."
}

$BundlePaths = @($BundleManifest.entries | ForEach-Object { ([string]$_.path).Replace("\", "/") })
foreach ($SkillName in $RequiredRepositorySecuritySkills) {
  if ($BundlePaths -notcontains ".agents/skills/$SkillName/SKILL.md") {
    throw "Required repository security skill is missing from the install bundle: $SkillName"
  }
}
foreach ($EntryPath in $BundlePaths) {
  $EntryPathKey = $EntryPath.ToLowerInvariant()
  if ($EntryPathKey -eq "capability-routing" -or
      $EntryPathKey.StartsWith("capability-routing/", [StringComparison]::Ordinal) -or
      $EntryPathKey -eq "capability-index" -or
      $EntryPathKey.StartsWith("capability-index/", [StringComparison]::Ordinal) -or
      $EntryPathKey -eq "hooks/capability-router" -or
      $EntryPathKey.StartsWith("hooks/capability-router/", [StringComparison]::Ordinal)) {
    throw "Dormant or retired routing source cannot enter the install bundle: $EntryPath"
  }
  if ($EntryPath -match "(^|/)plugins/cache(/|$)" -or
      $EntryPath -match "(^|/)(patches/)?external-skills/(codex-security|supabase|neon-postgres)(/|$)" -or
      $EntryPath -match "(^|/)(\.codex-plugin|[^/]+\.app\.json|[^/]+\.mcp\.json)$") {
    throw "Codex-managed plugin payload cannot enter the install bundle: $EntryPath"
  }
  foreach ($SkillName in $CodexManagedPluginSkillDirectories) {
    $PluginSkillRoot = ".agents/skills/$($SkillName.ToLowerInvariant())"
    if ($EntryPathKey -eq $PluginSkillRoot -or $EntryPathKey.StartsWith("$PluginSkillRoot/", [StringComparison]::Ordinal)) {
      throw "Codex-managed plugin skill body cannot enter the install bundle: $EntryPath"
    }
  }
}

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
if (-not $GitCommand -or -not (Test-Path $GitRoot)) {
  throw "Deterministic release packaging requires an exact clean Git checkout."
}

$GitStatus = @(& git -C $RepoRoot status --porcelain --untracked-files=all)
if ($LASTEXITCODE -ne 0) {
  throw "Could not inspect Git working tree before packaging."
}
if ($GitStatus.Count -gt 0) {
  throw "Git files, including untracked files, must match HEAD before packaging. Commit, remove, or ignore local files so validation and the archive use one reviewed revision."
}

& git -C $RepoRoot archive --format=zip --output=$OutputPath HEAD
if ($LASTEXITCODE -ne 0) {
  throw "git archive failed."
}

$RepeatPath = Join-Path ([System.IO.Path]::GetTempPath()) ("codex-coding-os-repeat-" + [guid]::NewGuid().ToString("N") + ".zip")
try {
  & git -C $RepoRoot archive --format=zip --output=$RepeatPath HEAD
  if ($LASTEXITCODE -ne 0) {
    throw "Repeated git archive failed."
  }

  $FirstDigest = (Get-FileHash -Algorithm SHA256 -LiteralPath $OutputPath).Hash
  $RepeatDigest = (Get-FileHash -Algorithm SHA256 -LiteralPath $RepeatPath).Hash
  if ($FirstDigest -ne $RepeatDigest) {
    throw "Repeated release archives are not byte-for-byte deterministic."
  }
} finally {
  if (Test-Path -LiteralPath $RepeatPath) {
    Remove-Item -LiteralPath $RepeatPath -Force
  }
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$ForbiddenExtensions = @($Manifest.release_safety.forbidden_file_extensions)
$Zip = [System.IO.Compression.ZipFile]::OpenRead($OutputPath)
try {
  $BadEntries = @()
  $ArchivePaths = @($Zip.Entries | ForEach-Object { $_.FullName.TrimEnd("/") })
  foreach ($Entry in $Zip.Entries) {
    $EntryName = $Entry.FullName
    $EntryNameKey = $EntryName.ToLowerInvariant()
    foreach ($Excluded in $ExcludedNames) {
      if ($EntryName -match "(^|/)$([regex]::Escape($Excluded))(/|$)") {
        $BadEntries += "Excluded path found in archive: $EntryName"
      }
    }

    $Ext = [System.IO.Path]::GetExtension($EntryName).ToLowerInvariant()
    if ($ForbiddenExtensions -contains $Ext) {
      $BadEntries += "Forbidden extension found in archive: $EntryName"
    }

    if ($EntryName -match "(^|/)hooks/capability-router(/|$)" -or
        $EntryName -match "(^|/)plugins/cache(/|$)" -or
        $EntryName -match "(^|/)(patches/)?external-skills/(codex-security|supabase|neon-postgres)(/|$)" -or
        $EntryName -match "(^|/)(\.codex-plugin|[^/]+\.app\.json|[^/]+\.mcp\.json)$") {
      $BadEntries += "Retired router or Codex-managed plugin payload found in source archive: $EntryName"
    }
    foreach ($SkillName in $CodexManagedPluginSkillDirectories) {
      $PluginSkillRoot = ".agents/skills/$($SkillName.ToLowerInvariant())"
      if ($EntryNameKey -eq $PluginSkillRoot -or $EntryNameKey.StartsWith("$PluginSkillRoot/", [StringComparison]::Ordinal)) {
        $BadEntries += "Codex-managed plugin skill body found in source archive: $EntryName"
      }
    }
  }

  foreach ($RequiredPath in @($Manifest.required_files)) {
    $NormalizedRequiredPath = ([string]$RequiredPath).Replace("\", "/")
    if ($ArchivePaths -notcontains $NormalizedRequiredPath) {
      $BadEntries += "Required source file is missing from release archive: $NormalizedRequiredPath"
    }
  }
  foreach ($RequiredPath in $RequiredDormantRoutingFiles) {
    if ($ArchivePaths -notcontains $RequiredPath) {
      $BadEntries += "Dormant capability-routing source is missing from release archive: $RequiredPath"
    }
  }
  foreach ($SkillName in $RequiredRepositorySecuritySkills) {
    if ($ArchivePaths -notcontains ".agents/skills/$SkillName/SKILL.md") {
      $BadEntries += "Repository security skill is missing from release archive: $SkillName"
    }
  }

  if ($ArchivePaths -notcontains "install-bundle.manifest.json") {
    $BadEntries += "install-bundle.manifest.json is missing from the release archive."
  }

  if ($BadEntries.Count -gt 0) {
    $BadEntries | ForEach-Object { Write-Output $_ }
    throw "Package archive inspection failed."
  }
} finally {
  $Zip.Dispose()
}

Write-Output "Packaged: $OutputPath"
