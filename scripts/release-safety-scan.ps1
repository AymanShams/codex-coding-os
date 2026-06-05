param(
  [string]$RepoRoot = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
  [switch]$RequireExternalScanners
)

$ErrorActionPreference = "Stop"

$ManifestPath = Join-Path $RepoRoot "pack.manifest.json"
if (-not (Test-Path $ManifestPath)) {
  throw "Missing pack manifest: $ManifestPath"
}

$Manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
$Errors = @()
$Warnings = @()

$ExcludedNames = @(".git", ".external-sources", ".release-exclusions.local.txt", ".private-terms.local.txt")
if ($Manifest.release_safety.excluded_paths) {
  $ExcludedNames += @($Manifest.release_safety.excluded_paths)
}
$ExcludedNames = $ExcludedNames | Select-Object -Unique

function Test-IsExcludedPath {
  param([Parameter(Mandatory = $true)][string]$Path)
  foreach ($Name in $ExcludedNames) {
    if ($Path -match "(^|[\\/])$([regex]::Escape($Name))([\\/]|$)") {
      return $true
    }
  }
  return $false
}

$Files = Get-ChildItem -Path $RepoRoot -Recurse -File -Force | Where-Object {
  -not (Test-IsExcludedPath -Path $_.FullName)
}

$ForbiddenExtensions = @($Manifest.release_safety.forbidden_file_extensions)
foreach ($File in $Files) {
  $Ext = $File.Extension.ToLowerInvariant()
  if ($ForbiddenExtensions -contains $Ext) {
    $Errors += "Forbidden release file extension found: $($File.FullName)"
  }
}

$SecretPatterns = @(
  @{ name = "OpenAI API key"; pattern = "sk-[A-Za-z0-9_-]{20,}" },
  @{ name = "OpenAI project API key"; pattern = "sk-proj-[A-Za-z0-9_-]{20,}" },
  @{ name = "GitHub classic token"; pattern = "ghp_[A-Za-z0-9_]{20,}" },
  @{ name = "GitHub fine-grained token"; pattern = "github_pat_[A-Za-z0-9_]{20,}" },
  @{ name = "GitLab token"; pattern = "glpat-[A-Za-z0-9_-]{20,}" },
  @{ name = "AWS access key"; pattern = "\b(AKIA|ASIA)[A-Z0-9]{16}\b" },
  @{ name = "Stripe secret key"; pattern = "\bsk_live_[A-Za-z0-9]{16,}\b" },
  @{ name = "Slack token"; pattern = "\bxox[baprs]-[A-Za-z0-9-]{20,}\b" },
  @{ name = "npm token"; pattern = "\bnpm_[A-Za-z0-9]{20,}\b" },
  @{ name = "Google service account"; pattern = '"type"\s*:\s*"service_account"' },
  @{ name = "Private key block"; pattern = "-----BEGIN (RSA |EC |OPENSSH |DSA |PRIVATE )?PRIVATE KEY-----" },
  @{ name = "Supabase service role"; pattern = "SUPABASE_SERVICE_ROLE_KEY\s*=" },
  @{ name = "Explicit OpenAI API key assignment"; pattern = "OPENAI_API_KEY\s*=\s*[^<\s]" },
  @{ name = "JWT-like token"; pattern = "\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\b" }
)

foreach ($PatternInfo in $SecretPatterns) {
  $Hits = $Files | Select-String -Pattern $PatternInfo.pattern -ErrorAction SilentlyContinue
  foreach ($Hit in $Hits) {
    $Errors += "Possible $($PatternInfo.name) in $($Hit.Path):$($Hit.LineNumber)"
  }
}

$LocalExclusionPath = Join-Path $RepoRoot ".release-exclusions.local.txt"
if (Test-Path $LocalExclusionPath) {
  $ForbiddenTerms = Get-Content -LiteralPath $LocalExclusionPath | Where-Object {
    $_.Trim().Length -gt 0 -and -not $_.Trim().StartsWith("#")
  }

  foreach ($Term in $ForbiddenTerms) {
    $Hits = $Files | Select-String -Pattern $Term -SimpleMatch -ErrorAction SilentlyContinue
    foreach ($Hit in $Hits) {
      $Errors += "Restricted release term '$Term' found in $($Hit.Path):$($Hit.LineNumber)"
    }
  }
}

if (Get-Command gitleaks -ErrorAction SilentlyContinue) {
  & gitleaks detect --source $RepoRoot --no-git --redact --verbose
  if ($LASTEXITCODE -ne 0) {
    $Errors += "gitleaks reported findings."
  }
} elseif ($RequireExternalScanners) {
  $Errors += "gitleaks is required but not installed."
} else {
  $Warnings += "gitleaks not installed; internal regex scan was used."
}

if (Get-Command trufflehog -ErrorAction SilentlyContinue) {
  & trufflehog filesystem --no-update --fail $RepoRoot
  if ($LASTEXITCODE -ne 0) {
    $Errors += "trufflehog reported findings."
  }
} elseif ($RequireExternalScanners) {
  $Errors += "trufflehog is required but not installed."
} else {
  $Warnings += "trufflehog not installed; internal regex scan was used."
}

if ($Warnings.Count -gt 0) {
  Write-Output "Release safety warnings:"
  $Warnings | ForEach-Object { Write-Output " - $_" }
}

if ($Errors.Count -gt 0) {
  Write-Output "Release safety scan failed:"
  $Errors | ForEach-Object { Write-Output " - $_" }
  exit 1
}

Write-Output "Release safety scan passed."
