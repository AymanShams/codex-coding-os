param(
  [string]$RepoRoot = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
)

$ErrorActionPreference = "Stop"
$Errors = @()
$Excluded = @(".git", ".external-sources")

function Test-IsExcluded {
  param([Parameter(Mandatory = $true)][string]$Path)
  foreach ($Name in $Excluded) {
    if ($Path -match "(^|[\\/])$([regex]::Escape($Name))([\\/]|$)") {
      return $true
    }
  }
  return $false
}

$MarkdownFiles = Get-ChildItem -Path $RepoRoot -Recurse -File -Filter "*.md" -Force | Where-Object {
  -not (Test-IsExcluded -Path $_.FullName)
}

foreach ($File in $MarkdownFiles) {
  $Text = Get-Content -Raw -LiteralPath $File.FullName
  $Text = [regex]::Replace($Text, '(?ms)^```.*?^```\s*$', '')
  $LinkMatches = [regex]::Matches($Text, '!?\[[^\]]*\]\(([^)\r\n]+)\)')
  foreach ($Match in $LinkMatches) {
    $Target = $Match.Groups[1].Value.Trim()
    if ($Target.StartsWith("<") -and $Target.EndsWith(">")) {
      $Target = $Target.Substring(1, $Target.Length - 2)
    } elseif ($Target -match '^(\S+)\s+["''].*["'']$') {
      $Target = $Matches[1]
    }

    if ($Target -match '^(#|https?://|mailto:|tel:|data:)' -or
        $Target -match '\{\{|\}\}|<[^>]+>' -or
        $Target.StartsWith("/")) {
      continue
    }

    $Target = $Target.Split("#")[0].Split("?")[0]
    if (-not $Target) {
      continue
    }

    $DecodedTarget = [System.Uri]::UnescapeDataString($Target)
    $ResolvedTarget = Join-Path $File.DirectoryName ($DecodedTarget -replace "/", [System.IO.Path]::DirectorySeparatorChar)
    if (-not (Test-Path -LiteralPath $ResolvedTarget)) {
      $RelativeFile = [System.IO.Path]::GetRelativePath($RepoRoot, $File.FullName)
      $Errors += "Broken internal link in ${RelativeFile}: $Target"
    }
  }
}

if ($Errors.Count -gt 0) {
  Write-Output "Internal link validation failed:"
  $Errors | Sort-Object -Unique | ForEach-Object { Write-Output " - $_" }
  exit 1
}

Write-Output "Internal link validation passed."
