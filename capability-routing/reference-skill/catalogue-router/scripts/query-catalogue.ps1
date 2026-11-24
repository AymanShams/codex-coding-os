param(
    [Parameter(Mandatory = $true)]
    [string]$Query,

    [AllowEmptyString()]
    [string]$TaskText,

    [string]$TaskInputPath,

    [switch]$BacklogOnly,
    [switch]$RouterOnly,
    [switch]$Json,
    [string]$ProjectId,
    [string]$Cwd,
    [ValidateSet('answer', 'transform', 'recall', 'research', 'synthesize', 'implement', 'review', 'extract', 'status')]
    [string]$TaskType,
    [ValidateSet('low', 'medium', 'high')]
    [string]$Complexity,
    [ValidateSet('none', 'memory', 'index', 'both')]
    [string]$SourceNeed,
    [ValidateSet('runtime_status', 'prior_continuity', 'project_evidence_lookup', 'retrieval_bundle', 'literal_structured_extraction', 'bounded_classification_or_transformation', 'complex_multi_source_synthesis', 'focused_coding_assistance', 'explicit_challenge', 'read_heavy_support')]
    [string]$LocalStackPurpose,
    [string[]]$SourceScope,
    [string[]]$ClassificationFlag,
    [ValidateSet('codex_only', 'worker_support')]
    [string]$ExecutionDisposition,
    [ValidateSet('local_agent_stack', 'terra', 'antigravity')]
    [string[]]$EligibleWorkerFamily,
    [switch]$ExactEvidence,
    [ValidateSet('none', 'recall', 'recall_and_capture')]
    [string]$MemoryMode,
    [ValidateSet('none', 'requested')]
    [string]$PersistenceIntent
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillDir = Split-Path -Parent $ScriptDir
$Catalogue = Join-Path $SkillDir 'references\capability-catalogue.md'
if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
    $CodexHome = [System.IO.Path]::GetFullPath($env:CODEX_HOME)
} elseif (-not [string]::IsNullOrWhiteSpace([string]$HOME)) {
    $CodexHome = Join-Path $HOME '.codex'
} else {
    Write-Error 'CODEX_HOME could not be resolved. Set CODEX_HOME to the active Codex home.'
    exit 1
}
$RouterCli = Join-Path $CodexHome 'hooks\capability_index_cli.py'

if (-not (Test-Path -LiteralPath $RouterCli)) {
    Write-Error "Canonical Catalogue Router not found: $RouterCli"
    exit 1
}

if ($TaskInputPath -and $PSBoundParameters.ContainsKey('TaskText')) {
    Write-Error 'TaskText is legacy conservative input and cannot be combined with TaskInputPath. Put executable text only in task_input.instruction.'
    exit 2
}

if ($RouterOnly) {
    $RouterArgs = @('-B', $RouterCli, '--query', $Query)
    if ($PSBoundParameters.ContainsKey('TaskText')) { $RouterArgs += @('--task-text', $TaskText) }
    if ($TaskInputPath) { $RouterArgs += @('--task-input-json', $TaskInputPath) }
    if ($Json) { $RouterArgs += '--json' }
    if ($ProjectId) { $RouterArgs += @('--project-id', $ProjectId) }
    if ($Cwd) { $RouterArgs += @('--cwd', $Cwd) }
    if ($TaskType) { $RouterArgs += @('--task-type', $TaskType) }
    if ($Complexity) { $RouterArgs += @('--complexity', $Complexity) }
    if ($SourceNeed) { $RouterArgs += @('--source-need', $SourceNeed) }
    if ($LocalStackPurpose) { $RouterArgs += @('--local-stack-purpose', $LocalStackPurpose) }
    foreach ($Scope in $SourceScope) { $RouterArgs += @('--source-scope', $Scope) }
    foreach ($Flag in $ClassificationFlag) { $RouterArgs += @('--classification-flag', $Flag) }
    if ($ExecutionDisposition) { $RouterArgs += @('--execution-disposition', $ExecutionDisposition) }
    foreach ($Family in $EligibleWorkerFamily) { $RouterArgs += @('--eligible-worker-family', $Family) }
    if ($ExactEvidence) { $RouterArgs += '--exact-evidence' }
    if ($MemoryMode) { $RouterArgs += @('--memory-mode', $MemoryMode) }
    if ($PersistenceIntent) { $RouterArgs += @('--persistence-intent', $PersistenceIntent) }
    & python @RouterArgs
    exit $LASTEXITCODE
}

if (-not $BacklogOnly) {
    $RouterArgs = @('-B', $RouterCli, '--query', $Query)
    if ($PSBoundParameters.ContainsKey('TaskText')) { $RouterArgs += @('--task-text', $TaskText) }
    if ($TaskInputPath) { $RouterArgs += @('--task-input-json', $TaskInputPath) }
    if ($Json) { $RouterArgs += '--json' }
    if ($ProjectId) { $RouterArgs += @('--project-id', $ProjectId) }
    if ($Cwd) { $RouterArgs += @('--cwd', $Cwd) }
    if ($TaskType) { $RouterArgs += @('--task-type', $TaskType) }
    if ($Complexity) { $RouterArgs += @('--complexity', $Complexity) }
    if ($SourceNeed) { $RouterArgs += @('--source-need', $SourceNeed) }
    if ($LocalStackPurpose) { $RouterArgs += @('--local-stack-purpose', $LocalStackPurpose) }
    foreach ($Scope in $SourceScope) { $RouterArgs += @('--source-scope', $Scope) }
    foreach ($Flag in $ClassificationFlag) { $RouterArgs += @('--classification-flag', $Flag) }
    if ($ExecutionDisposition) { $RouterArgs += @('--execution-disposition', $ExecutionDisposition) }
    foreach ($Family in $EligibleWorkerFamily) { $RouterArgs += @('--eligible-worker-family', $Family) }
    if ($ExactEvidence) { $RouterArgs += '--exact-evidence' }
    if ($MemoryMode) { $RouterArgs += @('--memory-mode', $MemoryMode) }
    if ($PersistenceIntent) { $RouterArgs += @('--persistence-intent', $PersistenceIntent) }
    & python @RouterArgs
    exit $LASTEXITCODE
}

if ($BacklogOnly) {
    if (-not (Test-Path -LiteralPath $Catalogue)) {
        Write-Error "Historical catalogue evidence not found: $Catalogue"
        exit 1
    }
    $lines = Get-Content -LiteralPath $Catalogue
    $start = ($lines | Select-String -Pattern '^## Candidate Backlog\s*:?\s*$' | Select-Object -First 1).LineNumber
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
    $searchLines | Select-String -Pattern $Query -Context 2,2 | ForEach-Object {
        $_.Context.PreContext
        $_.Line
        $_.Context.PostContext
    }
    exit 0
}

# BacklogOnly is retained for legacy discovery. Its output is evidence only and
# never overrides the canonical route printed by the default mode.
