param(
    [Parameter(Mandatory = $true)]
    [string]$Query,

    [switch]$BacklogOnly,
    [switch]$RouterOnly
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillDir = Split-Path -Parent $ScriptDir
$Catalogue = Join-Path $SkillDir 'references\capability-catalogue.md'

if (-not (Test-Path -LiteralPath $Catalogue)) {
    Write-Error "Catalogue not found: $Catalogue"
    exit 1
}

$lines = Get-Content -LiteralPath $Catalogue

if ($RouterOnly) {
    $start = ($lines | Select-String -Pattern '^## Fast Router$' | Select-Object -First 1).LineNumber
    if ($start) {
        $tail = $lines[($start - 1)..($lines.Count - 1)]
        $next = ($tail | Select-String -Pattern '^## ' | Select-Object -Skip 1 -First 1).LineNumber
        if ($next) {
            $tail[0..($next - 2)]
        } else {
            $tail
        }
    }
    exit 0
}

if ($BacklogOnly) {
    $start = ($lines | Select-String -Pattern '^## Candidate Backlog:' | Select-Object -First 1).LineNumber
    if (-not $start) {
        exit 0
    }
    $tail = $lines[($start - 1)..($lines.Count - 1)]
    $next = ($tail | Select-String -Pattern '^## ' | Select-Object -Skip 1 -First 1).LineNumber
    if ($next) {
        $searchLines = $tail[0..($next - 2)]
    } else {
        $searchLines = $tail
    }
    $searchLines | Select-String -Pattern $Query -Context 2,2
    exit 0
}

Select-String -LiteralPath $Catalogue -Pattern $Query -Context 2,2
