param(
  [string]$RepoRoot = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
  [switch]$RequireExternalScanners,
  [switch]$ScanGitHistory
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
  $NormalizedPath = $Path -replace "\\", "/"
  foreach ($Name in $ExcludedNames) {
    $NormalizedName = ([string]$Name) -replace "\\", "/"
    if ($NormalizedPath -match "(^|/)$([regex]::Escape($NormalizedName))(/|$)") {
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

$PublicInstallerPaths = @(
  "install-bundle.manifest.json",
  "universal",
  "scripts\install_transaction.py",
  "scripts\install.ps1",
  "scripts\install.sh",
  "scripts\uninstall.ps1",
  "scripts\uninstall.sh",
  "tests\test_install_transaction.py",
  "tests\install-transaction-faults.ps1"
)
$PublicInstallerFiles = foreach ($Relative in $PublicInstallerPaths) {
  $Path = Join-Path $RepoRoot $Relative
  if (Test-Path $Path -PathType Leaf) { Get-Item -LiteralPath $Path }
  elseif (Test-Path $Path -PathType Container) { Get-ChildItem -LiteralPath $Path -Recurse -File -Force }
}
$PrivateInstallerPatterns = @(
  @{ name = "committed Windows absolute path"; pattern = "[A-Za-z]:[\\/](Users|DEV|Work)[\\/]" },
  @{ name = "committed Unix home or temp path"; pattern = "(^|[\s`"'])/(Users|home|tmp|var|mnt|Volumes)/" },
  @{ name = "generated local provenance"; pattern = '"(case_id|repo_root|transaction_id|installed_at)"\s*:\s*"[^<]' }
)
foreach ($PatternInfo in $PrivateInstallerPatterns) {
  $Hits = $PublicInstallerFiles | Select-String -Pattern $PatternInfo.pattern -ErrorAction SilentlyContinue
  foreach ($Hit in $Hits) {
    $Errors += "Possible $($PatternInfo.name) in public installer file $($Hit.Path):$($Hit.LineNumber)"
  }
}

$ParallelAuditRoot = Join-Path $RepoRoot "docs\delivery\parallel-worktrees"
if (Test-Path $ParallelAuditRoot) {
  $AbsolutePathPatterns = @(
    @{ name = "Windows absolute path"; pattern = "\b[A-Za-z]:[\\/][^\s\)\]\}<>`"']+" },
    @{ name = "Unix user or temp absolute path"; pattern = "(^|[\s\(`"\'])/(Users|home|tmp|var|mnt|Volumes)/[^\s\)\]\}<>`"']+" }
  )
  $ParallelAuditFiles = Get-ChildItem -Path $ParallelAuditRoot -Recurse -File -Force
  foreach ($PatternInfo in $AbsolutePathPatterns) {
    $Hits = $ParallelAuditFiles | Select-String -Pattern $PatternInfo.pattern -ErrorAction SilentlyContinue
    foreach ($Hit in $Hits) {
      $Errors += "Possible $($PatternInfo.name) in committed parallel-lane audit file $($Hit.Path):$($Hit.LineNumber)"
    }
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

$CandidateHead = $null
if ($ScanGitHistory) {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    $Errors += "git is required for an exact candidate history scan."
  } else {
    $CandidateHead = (& git -C $RepoRoot rev-parse HEAD 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $CandidateHead -notmatch '^[0-9a-f]{40}$') {
      $Errors += "history scan could not resolve one exact candidate HEAD."
      $CandidateHead = $null
    }
    $CandidateStatus = @(& git -C $RepoRoot status --porcelain --untracked-files=all 2>$null)
    if ($LASTEXITCODE -ne 0) {
      $Errors += "history scan could not verify the candidate working tree."
    } elseif ($CandidateStatus.Count -gt 0) {
      $Errors += "history scan requires a clean committed candidate working tree."
    }
  }
}

if (Get-Command gitleaks -ErrorAction SilentlyContinue) {
  if ($ScanGitHistory) {
    if ($CandidateHead) {
      & gitleaks detect --source $RepoRoot --log-opts=$CandidateHead --redact --verbose
    }
  } else {
    & gitleaks detect --source $RepoRoot --no-git --redact --verbose
  }
  if ((-not $ScanGitHistory -or $CandidateHead) -and $LASTEXITCODE -ne 0) {
    $Errors += "gitleaks reported findings."
  }
} elseif ($RequireExternalScanners) {
  $Errors += "gitleaks is required but not installed."
} else {
  $Warnings += "gitleaks not installed; internal regex scan was used."
}

if (Get-Command trufflehog -ErrorAction SilentlyContinue) {
  $Python = Get-Command python -ErrorAction SilentlyContinue
  $TruffleHogGate = Join-Path $RepoRoot "scripts\trufflehog_result_gate.py"
  if (-not $Python) {
    $Errors += "python is required for redacted TruffleHog result evaluation."
  } elseif (-not (Test-Path -LiteralPath $TruffleHogGate -PathType Leaf)) {
    $Errors += "TruffleHog result gate is missing: $TruffleHogGate"
  } elseif ($ScanGitHistory) {
    if ($CandidateHead) {
      Write-Output "TruffleHog history scan target: exact local candidate ancestry"
      $AbsoluteUri = ([System.Uri](Resolve-Path -LiteralPath $RepoRoot).Path).AbsoluteUri
      if ([IO.Path]::DirectorySeparatorChar -eq [char]92) {
        $HistoryTarget = $AbsoluteUri -replace '^file:///', 'file://'
      } else {
        $HistoryTarget = $AbsoluteUri
      }
      $AllowlistPath = Join-Path $RepoRoot "scripts\release-safety-trufflehog-allowlist.json"
      $TruffleHogOutput = @(
        & trufflehog git $HistoryTarget --no-update --json --no-color `
          --log-level=-1 --fail-on-scan-errors --branch=$CandidateHead `
          --concurrency=1 2>&1
      )
      $TruffleHogExit = $LASTEXITCODE
      if ($TruffleHogExit -ne 0) {
        $Errors += "trufflehog history scan failed with exit code $TruffleHogExit."
      } else {
        $GateOutput = @(
          $TruffleHogOutput | & python -B $TruffleHogGate `
            --mode history --allowlist $AllowlistPath `
            --candidate-head $CandidateHead
        )
        $GateExit = $LASTEXITCODE
        $GateOutput | ForEach-Object { Write-Output $_ }
        if ($GateExit -ne 0) {
          $Errors += "trufflehog history result gate rejected the scan."
        }
      }
    }
  } else {
    $TruffleHogOutput = @(
      & trufflehog filesystem --no-update --json --no-color `
        --log-level=-1 --fail-on-scan-errors $RepoRoot 2>&1
    )
    $TruffleHogExit = $LASTEXITCODE
    if ($TruffleHogExit -ne 0) {
      $Errors += "trufflehog current-source scan failed with exit code $TruffleHogExit."
    } else {
      $GateOutput = @(
        $TruffleHogOutput | & python -B $TruffleHogGate --mode current
      )
      $GateExit = $LASTEXITCODE
      $GateOutput | ForEach-Object { Write-Output $_ }
      if ($GateExit -ne 0) {
        $Errors += "trufflehog current-source result gate rejected the scan."
      }
    }
  }
} elseif ($RequireExternalScanners) {
  $Errors += "trufflehog is required but not installed."
} else {
  $Warnings += "trufflehog not installed; internal regex scan was used."
}

if ($ScanGitHistory -and -not (Test-Path (Join-Path $RepoRoot ".git"))) {
  $Warnings += "Git history scan requested, but .git was not found under the repository root."
}

if ($CandidateHead) {
  $FinalHead = (& git -C $RepoRoot rev-parse HEAD 2>$null).Trim()
  $FinalStatus = @(& git -C $RepoRoot status --porcelain --untracked-files=all 2>$null)
  if ($LASTEXITCODE -ne 0 -or $FinalHead -ne $CandidateHead) {
    $Errors += "candidate HEAD changed during the history scan."
  }
  if ($FinalStatus.Count -gt 0) {
    $Errors += "candidate working tree changed during the history scan."
  }
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
