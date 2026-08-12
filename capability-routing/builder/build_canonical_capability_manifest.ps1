param(
    [string]$WorkspaceRoot = '',
    [string]$CodexHome = '',
    [string]$CodexExe = '',
    [string]$SkillsCsvPath = '',
    [string]$PluginsCsvPath = '',
    [string]$ToolsCsvPath = '',
    [string]$ConfigPath = '',
    [string]$ManifestPath = '',
    [string]$McpInventoryJsonPath = '',
    [string]$PluginInventoryJsonPath = '',
    [string]$RouterPythonExe = '',
    [string]$ConfigFingerprintModulePath = '',
    [string]$ExpectedAuthoritySnapshotSha256 = '',
    [string]$GeneratedAt = ''
)

$ErrorActionPreference = 'Stop'

if (-not $CodexHome) {
    $CodexHome = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex'
}

function Get-Sha256OrEmpty {
    param([string]$Path)
    if ($Path -and (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
    }
    return ''
}

function Get-RequiredSha256 {
    param([string]$Path, [string]$Label)
    $Hash = Get-Sha256OrEmpty -Path $Path
    if (-not $Hash) {
        throw "Required live source is missing or is not a file: $Label -> $Path"
    }
    return $Hash
}

function Get-TextSha256 {
    param([string]$Text)
    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
    $Hash = [System.Security.Cryptography.SHA256]::HashData($Bytes)
    return [Convert]::ToHexString($Hash)
}

function Resolve-RouterPythonExecutable {
    param([string]$Candidate, [string]$HooksPath)
    if ($Candidate -and (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        return (Resolve-Path -LiteralPath $Candidate).Path
    }
    if ($Candidate) {
        $Explicit = Get-Command -Name $Candidate -ErrorAction SilentlyContinue
        if ($Explicit) { return $Explicit.Source }
    }
    if (Test-Path -LiteralPath $HooksPath -PathType Leaf) {
        try {
            $Hooks = Get-Content -Raw -LiteralPath $HooksPath | ConvertFrom-Json -Depth 30
            foreach ($Group in @($Hooks.hooks.UserPromptSubmit)) {
                foreach ($Hook in @($Group.hooks)) {
                    $Command = [string]$Hook.commandWindows
                    if ($Command -notmatch 'user_prompt_skill_router\.py') { continue }
                    if ($Command -match '^\s*"(?<python>[^"]+python(?:\d+)?\.exe)"') {
                        $PythonPath = [string]$Matches.python
                        if (Test-Path -LiteralPath $PythonPath -PathType Leaf) {
                            return (Resolve-Path -LiteralPath $PythonPath).Path
                        }
                    }
                }
            }
        }
        catch {
            throw "Cannot resolve router Python from hooks.json: $($_.Exception.Message)"
        }
    }
    foreach ($Name in @('python3', 'python')) {
        $Command = Get-Command -Name $Name -ErrorAction SilentlyContinue
        if ($Command) { return $Command.Source }
    }
    throw 'Router Python executable could not be resolved.'
}

function Get-CapabilityConfigAuthority {
    param(
        [string]$PythonExe,
        [string]$ModulePath,
        [string]$Path
    )
    if (-not (Test-Path -LiteralPath $ModulePath -PathType Leaf)) {
        throw "Capability config fingerprint module is missing: $ModulePath"
    }
    $Raw = (& $PythonExe -B $ModulePath $Path 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $Raw) {
        throw "Capability config fingerprint failed with exit code ${LASTEXITCODE}: $Raw"
    }
    try {
        $Authority = $Raw | ConvertFrom-Json -Depth 30
    }
    catch {
        throw "Capability config fingerprint returned invalid JSON: $($_.Exception.Message)"
    }
    if ([string]$Authority.projection_schema -ne 'capability-config-v1' -or
        [string]$Authority.source_hash_key -ne 'config-capability-projection-v1' -or
        [string]$Authority.hash_scope -ne 'capability-config-v1' -or
        [string]$Authority.sha256 -notmatch '^[A-Fa-f0-9]{64}$' -or
        [string]$Authority.raw_sha256 -notmatch '^[A-Fa-f0-9]{64}$') {
        throw 'Capability config fingerprint returned an invalid authority contract.'
    }
    return $Authority
}

function Get-RecoveryAuthorityReceipt {
    param(
        [string]$PythonExe,
        [string]$ModulePath,
        [string]$CodexHomePath
    )
    if (-not (Test-Path -LiteralPath $ModulePath -PathType Leaf)) {
        throw "Capability recovery module is missing: $ModulePath"
    }
    $Raw = (& $PythonExe -B $ModulePath --snapshot --codex-home $CodexHomePath 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $Raw) {
        throw "Capability recovery snapshot failed with exit code ${LASTEXITCODE}: $Raw"
    }
    try {
        $Receipt = $Raw | ConvertFrom-Json -Depth 100
    }
    catch {
        throw "Capability recovery snapshot returned invalid JSON: $($_.Exception.Message)"
    }
    if ([string]$Receipt.schema_version -ne 'capability-authority-receipt-v2' -or
        [string]$Receipt.snapshot_sha256 -notmatch '^[A-Fa-f0-9]{64}$' -or
        -not [bool]$Receipt.app_identity.coherent) {
        throw 'Capability recovery snapshot returned an invalid authority receipt.'
    }
    return $Receipt
}

function Get-Provider {
    param([string]$Name, [string]$Provenance)
    if ($Name -like 'superpowers:*') { return 'superpowers' }
    if ($Name -like 'example-skills:*') { return 'example-skills' }
    if ($Name -match '^([^:]+):') { return $Matches[1] }
    if ($Provenance) { return $Provenance }
    return 'local'
}

function Get-VersionFromPath {
    param([string]$Path)
    $Normalized = $Path -replace '/', '\'
    if ($Normalized -match '\\([0-9]+(?:\.[0-9]+){1,3}(?:-[^\\]+)?)\\skills\\') {
        return $Matches[1]
    }
    if ($Normalized -match '\\(26\.[0-9]+\.[0-9]+)\\') {
        return $Matches[1]
    }
    return 'local'
}

function Normalize-LiveName {
    param([string]$Name)
    return (($Name ?? '').ToLowerInvariant() -replace '[^a-z0-9]', '')
}

function Resolve-CodexExecutable {
    param([string]$Candidate)
    if ($Candidate -and (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        return (Resolve-Path -LiteralPath $Candidate).Path
    }
    if ($Candidate) {
        $ExplicitCommand = Get-Command -Name $Candidate -ErrorAction SilentlyContinue
        if ($ExplicitCommand) { return $ExplicitCommand.Source }
    }
    if ($env:LOCALAPPDATA) {
        $BundledBinRoot = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin'
        if (Test-Path -LiteralPath $BundledBinRoot -PathType Container) {
            $BundledExecutable = Get-ChildItem -LiteralPath $BundledBinRoot -Filter 'codex.exe' -File -Recurse |
                Sort-Object LastWriteTimeUtc, FullName -Descending |
                Select-Object -First 1
            if ($BundledExecutable) { return $BundledExecutable.FullName }
        }
    }
    $Command = Get-Command -Name 'codex' -ErrorAction SilentlyContinue
    if ($Command) { return $Command.Source }
    throw 'Codex executable could not be resolved. Pass -CodexExe or provide both MCP and plugin inventory fixture paths.'
}

function Get-InventoryJsonText {
    param(
        [string]$FixturePath,
        [string[]]$Arguments,
        [string]$Label
    )
    if ($FixturePath) {
        if (-not (Test-Path -LiteralPath $FixturePath -PathType Leaf)) {
            throw "$Label fixture is missing: $FixturePath"
        }
        return (Get-Content -Raw -LiteralPath $FixturePath).Trim()
    }
    if (-not $script:ResolvedCodexExe) {
        $script:ResolvedCodexExe = Resolve-CodexExecutable -Candidate $CodexExe
    }
    $Raw = (& $script:ResolvedCodexExe @Arguments 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $Raw) {
        throw "Live $Label inventory failed with exit code $LASTEXITCODE."
    }
    return $Raw
}

function ConvertFrom-InventoryJson {
    param([string]$Json, [string]$Label)
    try {
        return $Json | ConvertFrom-Json -Depth 100
    }
    catch {
        throw "Live $Label inventory was not valid JSON: $($_.Exception.Message)"
    }
}

function Get-SkillFrontmatter {
    param([string]$Path, [string]$FallbackName)
    $Name = $FallbackName
    $Description = ''
    $Lines = @(Get-Content -LiteralPath $Path -TotalCount 80)
    if ($Lines.Count -gt 0 -and $Lines[0].Trim() -eq '---') {
        for ($Index = 1; $Index -lt $Lines.Count; $Index++) {
            $Line = [string]$Lines[$Index]
            if ($Line.Trim() -eq '---') { break }
            if ($Line -match '^name:\s*(?<value>.+?)\s*$') {
                $Name = $Matches.value.Trim().Trim('"').Trim("'")
                continue
            }
            if ($Line -match '^description:\s*(?<value>.+?)\s*$') {
                $Description = $Matches.value.Trim().Trim('"').Trim("'")
            }
        }
    }
    return [pscustomobject]@{ name = $Name; description = $Description }
}

function Get-PluginPackage {
    param([string]$Root, [string]$ActivationBasis, [string]$MarketplaceOverride = '')
    $ManifestPath = Get-PluginManifestPath -SourcePath $Root
    if (-not $ManifestPath) { return $null }
    try {
        $Manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json -Depth 30
    }
    catch {
        return $null
    }
    $Name = [string]$Manifest.name
    if (-not $Name) { $Name = Split-Path -Leaf $Root }
    $Version = [string]$Manifest.version
    if (-not $Version) { $Version = Split-Path -Leaf $Root }
    $Marketplace = $MarketplaceOverride
    $NormalizedRoot = $Root -replace '/', '\'
    if (-not $Marketplace -and $NormalizedRoot -match '\\plugins\\cache\\(?<marketplace>[^\\]+)\\') {
        $Marketplace = $Matches.marketplace
    }
    if (-not $Marketplace) { $Marketplace = 'local' }
    return [pscustomobject][ordered]@{
        name = $Name
        marketplace = $Marketplace
        version = $Version
        root = [System.IO.Path]::GetFullPath($Root)
        manifest_path = $ManifestPath
        activation_basis = $ActivationBasis
    }
}

function Set-PluginPackageCandidate {
    param(
        [hashtable]$Packages,
        [hashtable]$Priorities,
        [object]$Candidate,
        [int]$Priority,
        [hashtable]$DisabledPluginNames
    )
    if (-not $Candidate) { return }
    $Name = [string]$Candidate.name
    if (-not $Name -or $DisabledPluginNames.ContainsKey($Name)) { return }
    if (-not $Priorities.ContainsKey($Name) -or $Priority -gt [int]$Priorities[$Name]) {
        $Packages[$Name] = $Candidate
        $Priorities[$Name] = $Priority
    }
}

function Get-PassiveSkillInventory {
    param(
        [string]$CodexHomePath,
        [hashtable]$PluginPackages,
        [hashtable]$DisabledSkillPathKeys
    )
    $Rows = [System.Collections.Generic.List[object]]::new()
    $Seen = @{}

    $LocalRoots = @(
        [pscustomobject]@{ path = Join-Path $CodexHomePath 'skills'; basis = 'passive-local-skill-root' },
        [pscustomobject]@{ path = Join-Path (Split-Path -Parent $CodexHomePath) '.agents\skills'; basis = 'passive-agent-skill-root' },
        [pscustomobject]@{ path = Join-Path $CodexHomePath 'skills\.system'; basis = 'passive-system-skill-root' }
    )
    foreach ($Root in $LocalRoots) {
        if (-not (Test-Path -LiteralPath $Root.path -PathType Container)) { continue }
        foreach ($SkillDirectory in Get-ChildItem -LiteralPath $Root.path -Directory | Sort-Object Name) {
            if ($Root.basis -eq 'passive-local-skill-root' -and $SkillDirectory.Name -eq '.system') { continue }
            $SkillPath = Join-Path $SkillDirectory.FullName 'SKILL.md'
            if (-not (Test-Path -LiteralPath $SkillPath -PathType Leaf)) { continue }
            $SkillPathKey = [System.IO.Path]::GetFullPath($SkillPath)
            if ($DisabledSkillPathKeys.ContainsKey($SkillPathKey)) { continue }
            $Metadata = Get-SkillFrontmatter -Path $SkillPath -FallbackName $SkillDirectory.Name
            $Name = [string]$Metadata.name
            if (-not $Name -or $Seen.ContainsKey($Name)) { continue }
            $Seen[$Name] = $true
            $Rows.Add([pscustomobject][ordered]@{
                name = $Name
                description = [string]$Metadata.description
                path = (Resolve-Path -LiteralPath $SkillPath).Path
                activation_basis = $Root.basis
            })
        }
    }

    foreach ($PluginName in @($PluginPackages.Keys | Sort-Object)) {
        $Plugin = $PluginPackages[$PluginName]
        $SkillsRoot = Join-Path $Plugin.root 'skills'
        if (-not (Test-Path -LiteralPath $SkillsRoot -PathType Container)) { continue }
        foreach ($SkillDirectory in Get-ChildItem -LiteralPath $SkillsRoot -Directory | Sort-Object Name) {
            $SkillPath = Join-Path $SkillDirectory.FullName 'SKILL.md'
            if (-not (Test-Path -LiteralPath $SkillPath -PathType Leaf)) { continue }
            $SkillPathKey = [System.IO.Path]::GetFullPath($SkillPath)
            if ($DisabledSkillPathKeys.ContainsKey($SkillPathKey)) { continue }
            $Metadata = Get-SkillFrontmatter -Path $SkillPath -FallbackName $SkillDirectory.Name
            $Name = "${PluginName}:$([string]$Metadata.name)"
            if ($Seen.ContainsKey($Name)) { continue }
            $Seen[$Name] = $true
            $Rows.Add([pscustomobject][ordered]@{
                name = $Name
                description = [string]$Metadata.description
                path = (Resolve-Path -LiteralPath $SkillPath).Path
                activation_basis = [string]$Plugin.activation_basis
            })
        }
    }
    if ($Rows.Count -eq 0) { throw 'Passive capability roots produced zero current skill files.' }
    return [pscustomobject]@{ rows = @($Rows); missing = @() }
}

function Assert-PassiveCacheObjectWithinRoot {
    param(
        [System.IO.FileSystemInfo]$Item,
        [string]$ResolvedCacheRoot
    )
    $CandidatePath = $Item.FullName
    if ($Item.LinkType) {
        $ResolvedTarget = $Item.ResolveLinkTarget($true)
        if (-not $ResolvedTarget) {
            throw "Plugin cache link target could not be resolved: $($Item.FullName)"
        }
        $CandidatePath = $ResolvedTarget.FullName
    }
    $Root = [System.IO.Path]::GetFullPath($ResolvedCacheRoot).TrimEnd('\', '/')
    $Candidate = [System.IO.Path]::GetFullPath($CandidatePath).TrimEnd('\', '/')
    $RootPrefix = $Root + [System.IO.Path]::DirectorySeparatorChar
    if ($Candidate -ne $Root -and -not $Candidate.StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Plugin cache authority path resolves outside the cache root: $($Item.FullName)"
    }
}

function Get-PluginCacheInventoryHash {
    param([string]$CodexHomePath)
    $Rows = [System.Collections.Generic.List[string]]::new()
    $CacheRoot = Join-Path $CodexHomePath 'plugins\cache'
    if (Test-Path -LiteralPath $CacheRoot -PathType Container) {
        $ResolvedCacheRoot = (Resolve-Path -LiteralPath $CacheRoot).Path
        foreach ($Marketplace in Get-ChildItem -LiteralPath $CacheRoot -Directory | Sort-Object Name) {
            Assert-PassiveCacheObjectWithinRoot -Item $Marketplace -ResolvedCacheRoot $ResolvedCacheRoot
            foreach ($Plugin in Get-ChildItem -LiteralPath $Marketplace.FullName -Directory | Sort-Object Name) {
                Assert-PassiveCacheObjectWithinRoot -Item $Plugin -ResolvedCacheRoot $ResolvedCacheRoot
                if ($Plugin.Name -like 'plugin-install-*') { continue }
                foreach ($Version in Get-ChildItem -LiteralPath $Plugin.FullName -Directory | Sort-Object Name) {
                    Assert-PassiveCacheObjectWithinRoot -Item $Version -ResolvedCacheRoot $ResolvedCacheRoot
                    $PackageManifestPath = Join-Path $Version.FullName '.codex-plugin\plugin.json'
                    if (-not (Test-Path -LiteralPath $PackageManifestPath -PathType Leaf)) { continue }
                    $RelativeRoot = ([System.IO.Path]::GetRelativePath($CacheRoot, $Version.FullName) -replace '\\', '/').ToLowerInvariant()
                    $Rows.Add("ROOT`t$RelativeRoot`t0`t$($Version.LastWriteTimeUtc.Ticks)")
                    foreach ($RelativeFile in @('.codex-plugin\plugin.json', '.app.json', '.mcp.json')) {
                        $Path = Join-Path $Version.FullName $RelativeFile
                        if (Test-Path -LiteralPath $Path -PathType Leaf) {
                            $AuthorityDirectory = Get-Item -LiteralPath (Split-Path -Parent $Path)
                            Assert-PassiveCacheObjectWithinRoot -Item $AuthorityDirectory -ResolvedCacheRoot $ResolvedCacheRoot
                            $File = Get-Item -LiteralPath $Path
                            Assert-PassiveCacheObjectWithinRoot -Item $File -ResolvedCacheRoot $ResolvedCacheRoot
                            $Relative = ([System.IO.Path]::GetRelativePath($CacheRoot, $File.FullName) -replace '\\', '/').ToLowerInvariant()
                            $Rows.Add("FILE`t$Relative`t$($File.Length)`t$($File.LastWriteTimeUtc.Ticks)")
                        }
                    }
                    $SkillsRoot = Join-Path $Version.FullName 'skills'
                    if (Test-Path -LiteralPath $SkillsRoot -PathType Container) {
                        $SkillsDirectory = Get-Item -LiteralPath $SkillsRoot
                        Assert-PassiveCacheObjectWithinRoot -Item $SkillsDirectory -ResolvedCacheRoot $ResolvedCacheRoot
                        foreach ($SkillDirectory in Get-ChildItem -LiteralPath $SkillsRoot -Directory | Sort-Object Name) {
                            Assert-PassiveCacheObjectWithinRoot -Item $SkillDirectory -ResolvedCacheRoot $ResolvedCacheRoot
                            $SkillPath = Join-Path $SkillDirectory.FullName 'SKILL.md'
                            if (Test-Path -LiteralPath $SkillPath -PathType Leaf) {
                                $File = Get-Item -LiteralPath $SkillPath
                                Assert-PassiveCacheObjectWithinRoot -Item $File -ResolvedCacheRoot $ResolvedCacheRoot
                                $Relative = ([System.IO.Path]::GetRelativePath($CacheRoot, $File.FullName) -replace '\\', '/').ToLowerInvariant()
                                $Rows.Add("FILE`t$Relative`t$($File.Length)`t$($File.LastWriteTimeUtc.Ticks)")
                            }
                        }
                    }
                }
            }
        }
    }
    [string[]]$CanonicalRows = @($Rows)
    [Array]::Sort($CanonicalRows, [System.StringComparer]::Ordinal)
    return Get-TextSha256 -Text ($CanonicalRows -join "`n")
}

function Get-PluginManifestPath {
    param([string]$SourcePath)
    if (-not $SourcePath) { return '' }
    if (Test-Path -LiteralPath $SourcePath -PathType Leaf) {
        if ((Split-Path -Leaf $SourcePath) -eq 'plugin.json') {
            return (Resolve-Path -LiteralPath $SourcePath).Path
        }
        return ''
    }
    if (-not (Test-Path -LiteralPath $SourcePath -PathType Container)) { return '' }
    $Candidate = Join-Path $SourcePath '.codex-plugin\plugin.json'
    if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
        return (Resolve-Path -LiteralPath $Candidate).Path
    }
    return ''
}

function Test-McpTransportCallable {
    param([object]$Row)
    if (-not [bool]$Row.enabled) { return $false }
    $TransportType = [string]$Row.transport.type
    if ($TransportType -in @('streamable_http', 'http', 'sse')) {
        $Uri = $null
        return [System.Uri]::TryCreate([string]$Row.transport.url, [System.UriKind]::Absolute, [ref]$Uri) -and
            $Uri.Scheme -in @('http', 'https')
    }
    if ($TransportType -ne 'stdio') { return $false }

    $Command = [string]$Row.transport.command
    if (-not $Command) { return $false }
    if ([System.IO.Path]::IsPathRooted($Command)) {
        if (-not (Test-Path -LiteralPath $Command -PathType Leaf)) { return $false }
    }
    elseif (-not (Get-Command -Name $Command -ErrorAction SilentlyContinue)) {
        return $false
    }
    $Cwd = [string]$Row.transport.cwd
    if ($Cwd -and -not (Test-Path -LiteralPath $Cwd -PathType Container)) { return $false }
    foreach ($Argument in @($Row.transport.args)) {
        $ArgumentText = [string]$Argument
        if ($ArgumentText -and [System.IO.Path]::IsPathRooted($ArgumentText) -and
            -not (Test-Path -LiteralPath $ArgumentText)) {
            return $false
        }
    }
    return $true
}

function Get-McpSafeName {
    param([string]$Name)
    return ($Name.ToLowerInvariant() -replace '[^a-z0-9._-]', '-')
}

function Get-GatewayManagedMcpNames {
    param([string]$Path)
    $ManagedNames = [System.Collections.Generic.List[string]]::new()
    $CurrentName = $null
    $CurrentEnabled = $null
    $CurrentGatewayManaged = $false
    foreach ($Line in Get-Content -LiteralPath $Path) {
        if ($Line -match '^\s*\[') {
            if ($CurrentName -and $CurrentGatewayManaged -and $CurrentEnabled -eq $false) {
                $ManagedNames.Add($CurrentName)
            }
            $CurrentName = $null
            $CurrentEnabled = $null
            $CurrentGatewayManaged = $false
            if ($Line -match '^\s*\[mcp_servers\.(?:"(?<double>[^"]+)"|''(?<single>[^'']+)''|(?<plain>[^\]]+))\]\s*$') {
                $Name = if ($Matches.double) { $Matches.double } elseif ($Matches.single) { $Matches.single } else { $Matches.plain.Trim() }
                $CurrentName = $Name
            }
            continue
        }
        if (-not $CurrentName) { continue }
        if ($Line -match '^\s*enabled\s*=\s*(?<value>true|false)\s*(?:#.*)?$') {
            $CurrentEnabled = $Matches.value -eq 'true'
            continue
        }
        if ($Line -match '^\s*gateway_managed\s*=\s*(?<value>true|false)\s*(?:#.*)?$') {
            $CurrentGatewayManaged = $Matches.value -eq 'true'
        }
    }
    if ($CurrentName -and $CurrentGatewayManaged -and $CurrentEnabled -eq $false) {
        $ManagedNames.Add($CurrentName)
    }
    return @($ManagedNames | Sort-Object -Unique)
}

function Get-ExplicitlyDisabledMcpNames {
    param([string]$Path)
    $DisabledNames = [System.Collections.Generic.List[string]]::new()
    $CurrentName = $null
    $CurrentEnabled = $null
    foreach ($Line in Get-Content -LiteralPath $Path) {
        if ($Line -match '^\s*\[') {
            if ($CurrentName -and $CurrentEnabled -eq $false) {
                $DisabledNames.Add($CurrentName)
            }
            $CurrentName = $null
            $CurrentEnabled = $null
            if ($Line -match '^\s*\[mcp_servers\.(?:"(?<double>[^"]+)"|''(?<single>[^'']+)''|(?<plain>[^\]]+))\]\s*$' -or
                $Line -match '^\s*\[plugins\.(?:"[^"]+"|''[^'']+''|[^.\]]+)\.mcp_servers\.(?:"(?<double>[^"]+)"|''(?<single>[^'']+)''|(?<plain>[^\]]+))\]\s*$') {
                $CurrentName = if ($Matches.double) { $Matches.double } elseif ($Matches.single) { $Matches.single } else { $Matches.plain.Trim() }
            }
            continue
        }
        if ($CurrentName -and $Line -match '^\s*enabled\s*=\s*(?<value>true|false)\s*(?:#.*)?$') {
            $CurrentEnabled = $Matches.value -eq 'true'
        }
    }
    if ($CurrentName -and $CurrentEnabled -eq $false) {
        $DisabledNames.Add($CurrentName)
    }
    return @($DisabledNames | Sort-Object -Unique)
}

function Add-SourceHash {
    param(
        [System.Collections.Specialized.OrderedDictionary]$Target,
        [string]$Name,
        [string]$Path,
        [bool]$Required = $true
    )
    $Hash = Get-Sha256OrEmpty -Path $Path
    if (-not $Hash -and $Required) {
        throw "Required manifest source is missing: $Name -> $Path"
    }
    if ($Hash) { $Target[$Name] = $Hash }
}

if (-not $SkillsCsvPath -or -not $PluginsCsvPath -or -not $ToolsCsvPath) {
    throw 'SkillsCsvPath, PluginsCsvPath, and ToolsCsvPath are required. The repository does not ship user-specific universal inventory snapshots.'
}
if (-not $ConfigPath) { $ConfigPath = Join-Path $CodexHome 'config.toml' }
$HooksPath = Join-Path $CodexHome 'hooks.json'
$AgentsPath = Join-Path $CodexHome 'AGENTS.md'
$GatePath = Join-Path $CodexHome 'docs\context\task-routing-gate.md'
$RouterPath = Join-Path $CodexHome 'skills\catalogue-router\SKILL.md'
$CapabilityIndexPath = Join-Path $CodexHome 'hooks\capability_index.py'
$DefaultConfigFingerprintModulePath = Join-Path $CodexHome 'hooks\capability_config_fingerprint.py'
if (-not $ConfigFingerprintModulePath) { $ConfigFingerprintModulePath = $DefaultConfigFingerprintModulePath }
$CapabilityIndexCliPath = Join-Path $CodexHome 'hooks\capability_index_cli.py'
$PromptRouterPath = Join-Path $CodexHome 'hooks\user_prompt_skill_router.py'
$SessionStartPath = Join-Path $CodexHome 'hooks\capability_index_session_start.py'
$CommonModulePath = Join-Path $CodexHome 'hooks\_common.py'
$HookIoModulePath = Join-Path $CodexHome 'hooks\_hook_io.py'
$RecoveryModulePath = Join-Path $CodexHome 'hooks\capability_manifest_recovery.py'
$InstalledBuilderPath = $PSCommandPath
$AuthorityReceiptSchemaPath = Join-Path $CodexHome 'capability-routing\authority-receipt.schema.json'
$CatalogueQueryPath = Join-Path $CodexHome 'skills\catalogue-router\scripts\query-catalogue.ps1'
$DependencyGuardPath = Join-Path $CodexHome 'tools\dependency-readiness\ensure-node-dependencies.ps1'
$DependencyReadinessReadmePath = Join-Path $CodexHome 'tools\dependency-readiness\README.md'
$RoutingDir = Join-Path $CodexHome 'capability-routing'
if (-not $ManifestPath) { $ManifestPath = Join-Path $RoutingDir 'active-capabilities.json' }
$PolicyPath = Join-Path $RoutingDir 'routing-policy.yaml'
$RoutingPolicySchemaPath = Join-Path $RoutingDir 'routing-policy.schema.json'
$ActiveCapabilitiesSchemaPath = Join-Path $RoutingDir 'active-capabilities.schema.json'
$ProjectScopeMapPath = Join-Path $RoutingDir 'project-scope-map.json'
$ProjectScopeMapSchemaPath = Join-Path $RoutingDir 'project-scope-map.schema.json'
$RouteDecisionSchemaPath = Join-Path $RoutingDir 'route-decision.schema.json'

foreach ($RequiredCsv in @($SkillsCsvPath, $PluginsCsvPath, $ToolsCsvPath)) {
    if (-not (Test-Path -LiteralPath $RequiredCsv -PathType Leaf)) {
        throw "Required enrichment CSV is missing: $RequiredCsv"
    }
}
$ResolvedRouterPythonExe = Resolve-RouterPythonExecutable -Candidate $RouterPythonExe -HooksPath $HooksPath
$InitialRecoveryAuthority = Get-RecoveryAuthorityReceipt -PythonExe $ResolvedRouterPythonExe -ModulePath $RecoveryModulePath -CodexHomePath $CodexHome
if ($ExpectedAuthoritySnapshotSha256) {
    if ($ExpectedAuthoritySnapshotSha256 -notmatch '^[A-Fa-f0-9]{64}$') {
        throw 'ExpectedAuthoritySnapshotSha256 must be a SHA-256 value.'
    }
    if ([string]$InitialRecoveryAuthority.snapshot_sha256 -ne $ExpectedAuthoritySnapshotSha256) {
        throw 'Recovery authority did not match the expected stable snapshot at builder start.'
    }
    if (-not $CodexExe) {
        throw 'Recovery-bound builds require the exact receipt-bound Codex executable.'
    }
    $script:ResolvedCodexExe = Resolve-CodexExecutable -Candidate $CodexExe
    $ResolvedCodexCliId = Split-Path -Leaf (Split-Path -Parent $script:ResolvedCodexExe)
    $ResolvedCodexCliHash = Get-RequiredSha256 -Path $script:ResolvedCodexExe -Label 'receipt-bound Codex executable'
    if ($ResolvedCodexCliId -ne [string]$InitialRecoveryAuthority.app_identity.cli_id -or
        $ResolvedCodexCliHash -ne [string]$InitialRecoveryAuthority.app_identity.cli_executable_sha256) {
        throw 'Codex executable does not match the recovery authority receipt.'
    }
}
$InitialConfigAuthority = Get-CapabilityConfigAuthority -PythonExe $ResolvedRouterPythonExe -ModulePath $ConfigFingerprintModulePath -Path $ConfigPath
$ConfigHash = [string]$InitialConfigAuthority.raw_sha256
$ConfigCapabilityHash = [string]$InitialConfigAuthority.sha256
$ConfigCapabilitySourceHashKey = [string]$InitialConfigAuthority.source_hash_key
$GatewayManagedMcpNames = @($InitialConfigAuthority.gateway_managed_mcp_names)
$ExplicitlyDisabledMcpNames = @($InitialConfigAuthority.explicitly_disabled_mcp_names)
$DisabledSkillPathKeys = @{}
foreach ($DisabledSkillPath in @($InitialConfigAuthority.disabled_skill_paths)) {
    $DisabledSkillPathKeys[[System.IO.Path]::GetFullPath([string]$DisabledSkillPath)] = $true
}
$ExplicitlyDisabledMcpKeys = @{}
foreach ($DisabledMcpName in $ExplicitlyDisabledMcpNames) {
    $ExplicitlyDisabledMcpKeys[(Normalize-LiveName -Name $DisabledMcpName)] = $true
}

$McpJson = Get-InventoryJsonText -FixturePath $McpInventoryJsonPath -Arguments @('mcp', 'list', '--json') -Label 'MCP'
$PluginJson = Get-InventoryJsonText -FixturePath $PluginInventoryJsonPath -Arguments @('plugin', 'list', '--json') -Label 'plugin'
$McpRows = @(ConvertFrom-InventoryJson -Json $McpJson -Label 'MCP')
$PluginInventory = ConvertFrom-InventoryJson -Json $PluginJson -Label 'plugin'

$DatedSkillRows = @(Import-Csv -LiteralPath $SkillsCsvPath)
$DatedPluginRows = @(Import-Csv -LiteralPath $PluginsCsvPath)
$DatedToolRows = @(Import-Csv -LiteralPath $ToolsCsvPath)
$DatedSkillByName = @{}
foreach ($Row in $DatedSkillRows) {
    if (-not $DatedSkillByName.ContainsKey([string]$Row.exposed_name)) {
        $DatedSkillByName[[string]$Row.exposed_name] = $Row
    }
}
$DatedPluginByName = @{}
foreach ($Row in $DatedPluginRows) {
    if (-not $DatedPluginByName.ContainsKey([string]$Row.plugin)) {
        $DatedPluginByName[[string]$Row.plugin] = $Row
    }
}

$McpStateByName = @{}
$CallableMcpByName = @{}
$GatewayManagedMcpByName = @{}
$McpEvidenceByName = @{}
$SuppressedCapabilities = [System.Collections.Generic.List[object]]::new()
foreach ($Row in $McpRows) {
    $NormalizedName = Normalize-LiveName -Name ([string]$Row.name)
    if (-not $NormalizedName) { continue }
    $EffectiveEnabled = [bool]$Row.enabled -and -not $ExplicitlyDisabledMcpKeys.ContainsKey($NormalizedName)
    $McpStateByName[$NormalizedName] = $EffectiveEnabled
    if (-not $EffectiveEnabled) { continue }
    if (-not (Test-McpTransportCallable -Row $Row)) {
        $SuppressedCapabilities.Add([pscustomobject][ordered]@{
            id = "mcp:$(Get-McpSafeName -Name ([string]$Row.name))"
            reason_code = 'LIVE_MCP_TRANSPORT_UNRESOLVABLE'
            missing_capability = $null
            fallback_capabilities = @()
        })
        continue
    }
    $CallableMcpByName[$NormalizedName] = $Row
    if ($Row.transport.url) {
        $McpEvidenceByName[$NormalizedName] = [pscustomobject]@{
            source_path = [string]$Row.transport.url
            sha256 = Get-TextSha256 -Text ([string]$Row.transport.url)
            hash_scope = 'text-sha256'
        }
    }
    else {
        $McpEvidenceByName[$NormalizedName] = [pscustomobject]@{
            source_path = $ConfigPath
            sha256 = $ConfigCapabilityHash
            hash_scope = 'capability-config-v1'
        }
    }
}

$DisabledPluginNames = @{}
foreach ($Installed in @($PluginInventory.installed)) {
    if ([bool]$Installed.installed -and -not [bool]$Installed.enabled -and [string]$Installed.name) {
        $DisabledPluginNames[[string]$Installed.name] = $true
    }
}
$PluginRoots = @{}
$PluginPriorities = @{}
foreach ($Installed in @($PluginInventory.installed) | Sort-Object name, marketplaceName) {
    if (-not [bool]$Installed.installed -or -not [bool]$Installed.enabled) { continue }
    $Name = [string]$Installed.name
    if (-not $Name -or $Name -in @('example-skills', 'cb-insights', 'outlook-email')) { continue }
    $ExactCacheRoot = Join-Path $CodexHome "plugins\cache\$([string]$Installed.marketplaceName)\$Name\$([string]$Installed.version)"
    $Candidate = Get-PluginPackage -Root $ExactCacheRoot -ActivationBasis 'passive-live-plugin-list-exact-cache' -MarketplaceOverride ([string]$Installed.marketplaceName)
    if (-not $Candidate) {
        $Candidate = Get-PluginPackage -Root ([string]$Installed.source.path) -ActivationBasis 'passive-live-plugin-list-source' -MarketplaceOverride ([string]$Installed.marketplaceName)
    }
    # The plugin list proves that the parent plugin is enabled. Its package selector can
    # lag the prompt-active remote skill bundle, so it is lower authority than a resolved
    # MCP package root or an unambiguous current remote-cache package.
    Set-PluginPackageCandidate -Packages $PluginRoots -Priorities $PluginPriorities -Candidate $Candidate -Priority 200 -DisabledPluginNames $DisabledPluginNames
}

foreach ($Row in $McpRows) {
    $Cwd = [string]$Row.transport.cwd
    if (-not $Cwd -or -not (Test-Path -LiteralPath $Cwd -PathType Container)) { continue }
    $Candidate = Get-PluginPackage -Root ([System.IO.Path]::GetFullPath($Cwd)) -ActivationBasis 'passive-resolved-mcp-package-root'
    Set-PluginPackageCandidate -Packages $PluginRoots -Priorities $PluginPriorities -Candidate $Candidate -Priority 400 -DisabledPluginNames $DisabledPluginNames
}

$RemoteCacheRoot = Join-Path $CodexHome 'plugins\cache\openai-curated-remote'
if (Test-Path -LiteralPath $RemoteCacheRoot -PathType Container) {
    foreach ($PluginDirectory in Get-ChildItem -LiteralPath $RemoteCacheRoot -Directory | Sort-Object Name) {
        $Candidates = [System.Collections.Generic.List[object]]::new()
        foreach ($VersionDirectory in Get-ChildItem -LiteralPath $PluginDirectory.FullName -Directory | Sort-Object Name) {
            $Candidate = Get-PluginPackage -Root $VersionDirectory.FullName -ActivationBasis 'passive-remote-cache-single-version' -MarketplaceOverride 'openai-curated-remote'
            if ($Candidate) { $Candidates.Add($Candidate) }
        }
        if ($Candidates.Count -eq 1) {
            Set-PluginPackageCandidate -Packages $PluginRoots -Priorities $PluginPriorities -Candidate $Candidates[0] -Priority 300 -DisabledPluginNames $DisabledPluginNames
            continue
        }
        if ($Candidates.Count -gt 1) {
            $PluginName = [string]$Candidates[0].name
            if (-not $PluginPriorities.ContainsKey($PluginName) -or [int]$PluginPriorities[$PluginName] -lt 400) {
                $SuppressedCapabilities.Add([pscustomobject][ordered]@{
                    id = "plugin:$PluginName"
                    reason_code = 'PASSIVE_PLUGIN_VERSION_AMBIGUOUS'
                    source_path = $PluginDirectory.FullName
                    candidates = @($Candidates | ForEach-Object { [string]$_.version })
                })
                $PluginRoots.Remove($PluginName)
                $PluginPriorities.Remove($PluginName)
            }
        }
    }
}

$LiveSkillInventory = Get-PassiveSkillInventory -CodexHomePath $CodexHome -PluginPackages $PluginRoots -DisabledSkillPathKeys $DisabledSkillPathKeys
$GatewayKey = Normalize-LiveName -Name 'codex-stability-gateway'
if ($CallableMcpByName.ContainsKey($GatewayKey)) {
    foreach ($GatewayManagedName in $GatewayManagedMcpNames) {
        $ManagedKey = Normalize-LiveName -Name $GatewayManagedName
        $ResolvedRow = @($McpRows | Where-Object { (Normalize-LiveName -Name ([string]$_.name)) -eq $ManagedKey } | Select-Object -First 1)
        if ($ResolvedRow.Count -eq 0 -or [bool]$McpStateByName[$ManagedKey]) { continue }
        $GatewayManagedMcpByName[$ManagedKey] = $ResolvedRow[0]
        $CallableMcpByName[$ManagedKey] = $ResolvedRow[0]
        $McpEvidenceByName[$ManagedKey] = [pscustomobject]@{
            source_path = $ConfigPath
            sha256 = $ConfigCapabilityHash
            hash_scope = 'capability-config-v1'
        }
    }
}

$Entries = [System.Collections.Generic.List[object]]::new()

$SuppressedSkillDependencies = [ordered]@{
    'creative-production:intake' = [ordered]@{
        mcp = 'creative_production_mcp'
        fallback_capabilities = @('skill:imagegen')
    }
    'creative-production:produce' = [ordered]@{
        mcp = 'creative_production_mcp'
        fallback_capabilities = @('skill:imagegen')
    }
    'cloudflare:web-perf' = [ordered]@{
        mcp = 'chrome-devtools'
        fallback_capabilities = @('skill:playwright', 'skill:browser:control-in-app-browser')
    }
    'codex-security:deep-security-scan' = [ordered]@{
        mcp = 'codex-security'
        fallback_capabilities = @('skill:security-best-practices', 'skill:deep-critic', 'skill:evidence-checker')
    }
}

$FallbackSkillDependencies = [ordered]@{
    'openai-developers:openai-platform-api-key' = [ordered]@{
        mcp = 'openai-api-key-local-confirmation'
        fallback_mode = 'typed-user-confirmation'
    }
    'data-analytics:build-report' = [ordered]@{
        mcp = 'dataAnalyticsWidgets'
        fallback_mode = 'portable-html-static-table'
    }
    'data-analytics:build-dashboard' = [ordered]@{
        mcp = 'dataAnalyticsWidgets'
        fallback_mode = 'portable-html-static-table'
    }
    'data-analytics:visualize-data' = [ordered]@{
        mcp = 'dataAnalyticsWidgets'
        fallback_mode = 'matplotlib-static-table'
    }
    'data-analytics:publish-artifact-to-sites' = [ordered]@{
        mcp = 'dataAnalyticsWidgets'
        fallback_mode = 'owning-html-workflow'
    }
    'codex-security:security-scan' = [ordered]@{
        mcp = 'codex-security'
        fallback_mode = 'local-finalize-script'
    }
    'codex-security:security-diff-scan' = [ordered]@{
        mcp = 'codex-security'
        fallback_mode = 'local-finalize-script'
    }
    'codex-security:attack-path-analysis' = [ordered]@{
        mcp = 'codex-security'
        fallback_mode = 'bounded-filesystem-analysis'
        blocked_modes = @('deep-candidate')
    }
    'codex-security:validation' = [ordered]@{
        mcp = 'codex-security'
        fallback_mode = 'bounded-filesystem-analysis'
        blocked_modes = @('deep-candidate')
    }
    'codex-security:threat-model' = [ordered]@{
        mcp = 'codex-security'
        fallback_mode = 'filesystem-threat-model'
    }
}

$SuppressedSkillNames = @(
    'build-web-apps:supabase-postgres-best-practices'
)

foreach ($LiveSkill in @($LiveSkillInventory.rows)) {
    $Name = [string]$LiveSkill.name
    if ($Name -like 'example-skills:*' -or $Name -in $SuppressedSkillNames) { continue }
    $Dependency = $SuppressedSkillDependencies[$Name]
    if ($Dependency) {
        $DependencyKey = Normalize-LiveName -Name ([string]$Dependency.mcp)
        if (-not $CallableMcpByName.ContainsKey($DependencyKey)) {
            $SuppressedCapabilities.Add([pscustomobject][ordered]@{
                id = "skill:$Name"
                reason_code = 'HARD_DEPENDENCY_DISABLED_OR_UNCALLABLE'
                missing_capability = "mcp:$($Dependency.mcp)"
                fallback_capabilities = @($Dependency.fallback_capabilities)
            })
            continue
        }
    }

    $Dated = $DatedSkillByName[$Name]
    $Families = @()
    if ($Dated) {
        $Families = @([string]$Dated.capability_family -split '\s*\|\s*' | Where-Object { $_ })
    }
    if ($Families.Count -eq 0) { $Families = @('skill') }
    $Description = if ($Dated -and [string]$Dated.description) { [string]$Dated.description } else { [string]$LiveSkill.description }
    if ($Description.Length -gt 320) { $Description = $Description.Substring(0, 320) }
    $SkillHash = Get-RequiredSha256 -Path ([string]$LiveSkill.path) -Label "live skill $Name"
    $Entry = [ordered]@{
        id = "skill:$Name"
        kind = 'skill'
        name = $Name
        state = 'active-live'
        provider = Get-Provider -Name $Name -Provenance $(if ($Dated) { [string]$Dated.provenance } else { '' })
        version = Get-VersionFromPath -Path ([string]$LiveSkill.path)
        source_path = [string]$LiveSkill.path
        sha256 = $SkillHash
        families = @($Families | Select-Object -Unique)
        description = $Description
        activation_basis = [string]$LiveSkill.activation_basis
    }
    $FallbackPolicy = $FallbackSkillDependencies[$Name]
    if ($FallbackPolicy) {
        $FallbackKey = Normalize-LiveName -Name ([string]$FallbackPolicy.mcp)
        if (-not $CallableMcpByName.ContainsKey($FallbackKey)) {
            $Fallback = [ordered]@{
                dependency_state = 'fallback-active'
                unavailable_capabilities = @("mcp:$($FallbackPolicy.mcp)")
                fallback_mode = [string]$FallbackPolicy.fallback_mode
            }
            if ($FallbackPolicy.blocked_modes) {
                $Fallback.blocked_modes = @($FallbackPolicy.blocked_modes)
            }
            $Entry.execution_fallback = $Fallback
        }
    }
    $Entries.Add([pscustomobject]$Entry)
}

$MandatoryAppsByName = @{}
foreach ($PluginName in @($PluginRoots.Keys | Sort-Object)) {
    $Plugin = $PluginRoots[$PluginName]
    $ManifestData = Get-Content -Raw -LiteralPath $Plugin.manifest_path | ConvertFrom-Json -Depth 30
    $Dated = $DatedPluginByName[$PluginName]
    $Description = if ([string]$ManifestData.description) { [string]$ManifestData.description } elseif ($Dated) { [string]$Dated.description } else { '' }
    if ($Description.Length -gt 320) { $Description = $Description.Substring(0, 320) }
    $Families = @('plugin')
    if ($Dated -and [string]$Dated.router_scope) { $Families += [string]$Dated.router_scope }

    $SkillCount = @($LiveSkillInventory.rows | Where-Object {
        ([string]$_.path).StartsWith(([string]$Plugin.root), [System.StringComparison]::OrdinalIgnoreCase)
    }).Count
    $AppManifestPath = Join-Path $Plugin.root '.app.json'
    $McpManifestPath = Join-Path $Plugin.root '.mcp.json'
    $MandatoryAppNames = [System.Collections.Generic.List[string]]::new()
    $OptionalAppNames = [System.Collections.Generic.List[string]]::new()
    if (Test-Path -LiteralPath $AppManifestPath -PathType Leaf) {
        $AppData = Get-Content -Raw -LiteralPath $AppManifestPath | ConvertFrom-Json -Depth 30
        foreach ($AppProperty in @($AppData.apps.PSObject.Properties)) {
            if ([bool]$AppProperty.Value.optional) {
                $OptionalAppNames.Add([string]$AppProperty.Name)
                continue
            }
            $MandatoryAppNames.Add([string]$AppProperty.Name)
            $MandatoryAppsByName[(Normalize-LiveName -Name ([string]$AppProperty.Name))] = [pscustomobject]@{
                name = [string]$AppProperty.Name
                id = [string]$AppProperty.Value.id
                plugin = $PluginName
                source_path = (Resolve-Path -LiteralPath $AppManifestPath).Path
                sha256 = Get-RequiredSha256 -Path $AppManifestPath -Label "plugin app manifest $PluginName"
            }
        }
    }
    $DeclaredMcpStates = [System.Collections.Generic.List[object]]::new()
    if (Test-Path -LiteralPath $McpManifestPath -PathType Leaf) {
        $McpData = Get-Content -Raw -LiteralPath $McpManifestPath | ConvertFrom-Json -Depth 30
        foreach ($McpProperty in @($McpData.mcpServers.PSObject.Properties)) {
            $McpKey = Normalize-LiveName -Name ([string]$McpProperty.Name)
            $DeclaredMcpStates.Add([pscustomobject][ordered]@{
                name = [string]$McpProperty.Name
                state = if ($CallableMcpByName.ContainsKey($McpKey)) { 'active' } elseif ($McpStateByName.ContainsKey($McpKey)) { 'disabled' } else { 'not-configured' }
            })
        }
    }

    $Entries.Add([pscustomobject][ordered]@{
        id = "plugin:$PluginName"
        kind = 'plugin'
        name = if ([string]$ManifestData.name) { [string]$ManifestData.name } elseif ($Dated) { [string]$Dated.display_name } else { $PluginName }
        state = 'active-live'
        provider = [string]$Plugin.marketplace
        version = if ([string]$ManifestData.version) { [string]$ManifestData.version } else { [string]$Plugin.version }
        source_path = [string]$Plugin.manifest_path
        sha256 = Get-RequiredSha256 -Path ([string]$Plugin.manifest_path) -Label "plugin $PluginName"
        families = @($Families | Select-Object -Unique)
        description = $Description
        activation_basis = [string]$Plugin.activation_basis
        components = [ordered]@{
            skills = [ordered]@{ state = if ($SkillCount -gt 0) { 'active' } else { 'not-present' }; count = $SkillCount }
            apps = [ordered]@{ state = if ($MandatoryAppNames.Count -gt 0) { 'active' } else { 'not-present' }; active = @($MandatoryAppNames); optional = @($OptionalAppNames) }
            mcp_servers = @($DeclaredMcpStates)
        }
    })
}

foreach ($Row in $McpRows | Where-Object { $_.enabled -eq $true }) {
    $NormalizedName = Normalize-LiveName -Name ([string]$Row.name)
    if (-not $CallableMcpByName.ContainsKey($NormalizedName)) { continue }
    $Evidence = $McpEvidenceByName[$NormalizedName]
    $Transport = [string]$Row.transport.type
    $Entries.Add([pscustomobject][ordered]@{
        id = "mcp:$(Get-McpSafeName -Name ([string]$Row.name))"
        kind = 'mcp'
        name = [string]$Row.name
        state = 'active-live'
        provider = if ($Row.transport.url) { 'remote' } else { 'local-or-plugin' }
        version = 'runtime'
        source_path = [string]$Evidence.source_path
        sha256 = [string]$Evidence.sha256
        hash_scope = [string]$Evidence.hash_scope
        families = @('mcp', $Transport | Where-Object { $_ } | Select-Object -Unique)
        description = "Live configured MCP server using $Transport transport."
        activation_basis = 'live-config-and-codex-mcp-list-callable-transport'
    })
}
foreach ($ManagedKey in @($GatewayManagedMcpByName.Keys | Sort-Object)) {
    $Row = $GatewayManagedMcpByName[$ManagedKey]
    $Evidence = $McpEvidenceByName[$ManagedKey]
    $Entries.Add([pscustomobject][ordered]@{
        id = "mcp:$(Get-McpSafeName -Name ([string]$Row.name))"
        kind = 'mcp'
        name = [string]$Row.name
        state = 'active-gateway-managed'
        provider = 'codex-stability-gateway'
        version = 'runtime'
        source_path = [string]$Evidence.source_path
        sha256 = [string]$Evidence.sha256
        hash_scope = [string]$Evidence.hash_scope
        families = @('mcp', 'gateway-managed')
        description = 'Direct launch is disabled. The live stability gateway is the sole configured owner.'
        activation_basis = 'live-config-gateway-managed-and-live-gateway'
    })
}

foreach ($Row in $DatedToolRows) {
    $CapabilityId = [string]$Row.capability_id
    if ($CapabilityId -like 'tool-family:mcp:*') {
        $ServerName = $CapabilityId.Substring('tool-family:mcp:'.Length)
        $ServerKey = Normalize-LiveName -Name $ServerName
        if (-not $CallableMcpByName.ContainsKey($ServerKey)) { continue }
        $Evidence = $McpEvidenceByName[$ServerKey]
        $Description = [string]$Row.representative_purpose
        if ($Description.Length -gt 320) { $Description = $Description.Substring(0, 320) }
        $Entries.Add([pscustomobject][ordered]@{
            id = $CapabilityId
            kind = 'tool-family'
            name = [string]$Row.family
            state = 'active-live'
            provider = 'mcp'
            version = 'runtime'
            source_path = [string]$Evidence.source_path
            sha256 = [string]$Evidence.sha256
            hash_scope = [string]$Evidence.hash_scope
            families = @([string]$Row.surface_type, [string]$Row.activation_profile | Where-Object { $_ } | Select-Object -Unique)
            description = $Description
            activation_basis = 'live-config-and-codex-mcp-list-dated-metadata-only'
        })
        continue
    }
    if ($CapabilityId -like 'tool-family:app:*') {
        $AppName = $CapabilityId.Substring('tool-family:app:'.Length)
        $AppKey = Normalize-LiveName -Name $AppName
        if (-not $MandatoryAppsByName.ContainsKey($AppKey)) { continue }
        $App = $MandatoryAppsByName[$AppKey]
        $Description = [string]$Row.representative_purpose
        if ($Description.Length -gt 320) { $Description = $Description.Substring(0, 320) }
        $Entries.Add([pscustomobject][ordered]@{
            id = $CapabilityId
            kind = 'tool-family'
            name = [string]$Row.family
            state = 'active-live'
            provider = [string]$App.plugin
            version = 'runtime'
            source_path = [string]$App.source_path
            sha256 = [string]$App.sha256
            families = @([string]$Row.surface_type, [string]$Row.activation_profile | Where-Object { $_ } | Select-Object -Unique)
            description = $Description
            activation_basis = 'enabled-live-plugin-mandatory-app-dated-metadata-only'
            app_id = [string]$App.id
        })
    }
}

$DuplicateIds = $Entries | Group-Object id | Where-Object { $_.Count -gt 1 }
if ($DuplicateIds) {
    throw "Duplicate capability IDs: $($DuplicateIds.Name -join ', ')"
}
foreach ($Entry in $Entries) {
    if (-not [string]$Entry.sha256 -or [string]$Entry.sha256 -notmatch '^[A-Fa-f0-9]{64}$') {
        throw "Active entry has no valid source hash: $($Entry.id)"
    }
    if ([string]$Entry.source_path -notmatch '^https?://' -and -not (Test-Path -LiteralPath ([string]$Entry.source_path))) {
        throw "Active entry source does not exist: $($Entry.id) -> $($Entry.source_path)"
    }
}

$McpCanonical = @($McpRows | Sort-Object name | ForEach-Object {
    [ordered]@{
        name = [string]$_.name
        enabled = [bool]$_.enabled
        transport_type = [string]$_.transport.type
        command = [string]$_.transport.command
        url = [string]$_.transport.url
        cwd = [string]$_.transport.cwd
    }
}) | ConvertTo-Json -Depth 10 -Compress
$PluginCanonical = @($PluginInventory.installed | Sort-Object pluginId | ForEach-Object {
    [ordered]@{
        plugin_id = [string]$_.pluginId
        installed = [bool]$_.installed
        enabled = [bool]$_.enabled
        version = [string]$_.version
        source_path = [string]$_.source.path
    }
}) | ConvertTo-Json -Depth 10 -Compress
$SkillCanonical = @($LiveSkillInventory.rows | Sort-Object name | ForEach-Object {
    [ordered]@{
        name = [string]$_.name
        path = [string]$_.path
        sha256 = Get-RequiredSha256 -Path ([string]$_.path) -Label "live skill $($_.name)"
    }
}) | ConvertTo-Json -Depth 10 -Compress

$FinalConfigAuthority = Get-CapabilityConfigAuthority -PythonExe $ResolvedRouterPythonExe -ModulePath $ConfigFingerprintModulePath -Path $ConfigPath
if ([string]$FinalConfigAuthority.sha256 -ne $ConfigCapabilityHash) {
    throw 'Capability config semantics changed during live inventory. Manifest replacement is denied.'
}
$ConfigHash = [string]$FinalConfigAuthority.raw_sha256
$FinalRecoveryAuthority = Get-RecoveryAuthorityReceipt -PythonExe $ResolvedRouterPythonExe -ModulePath $RecoveryModulePath -CodexHomePath $CodexHome
if ([string]$FinalRecoveryAuthority.snapshot_sha256 -ne [string]$InitialRecoveryAuthority.snapshot_sha256) {
    throw 'Recovery authority changed during live inventory. Manifest replacement is denied.'
}
if ($ExpectedAuthoritySnapshotSha256 -and
    [string]$FinalRecoveryAuthority.snapshot_sha256 -ne $ExpectedAuthoritySnapshotSha256) {
    throw 'Recovery authority no longer matches the expected stable snapshot.'
}

$SourceHashes = [ordered]@{
    'hooks.json' = Get-Sha256OrEmpty -Path $HooksPath
}
if (-not $SourceHashes['hooks.json']) {
    throw "Required manifest source is missing: hooks.json -> $HooksPath"
}
Add-SourceHash -Target $SourceHashes -Name 'config.toml' -Path $ConfigPath
$SourceHashes[$ConfigCapabilitySourceHashKey] = $ConfigCapabilityHash
Add-SourceHash -Target $SourceHashes -Name 'AGENTS.md' -Path $AgentsPath
Add-SourceHash -Target $SourceHashes -Name 'task-routing-gate.md' -Path $GatePath
Add-SourceHash -Target $SourceHashes -Name 'catalogue-router.SKILL.md' -Path $RouterPath
Add-SourceHash -Target $SourceHashes -Name 'capability_index.py' -Path $CapabilityIndexPath
Add-SourceHash -Target $SourceHashes -Name 'capability_config_fingerprint.py' -Path $ConfigFingerprintModulePath
Add-SourceHash -Target $SourceHashes -Name 'capability_index_cli.py' -Path $CapabilityIndexCliPath
Add-SourceHash -Target $SourceHashes -Name 'user_prompt_skill_router.py' -Path $PromptRouterPath
Add-SourceHash -Target $SourceHashes -Name 'capability_index_session_start.py' -Path $SessionStartPath
Add-SourceHash -Target $SourceHashes -Name '_common.py' -Path $CommonModulePath
Add-SourceHash -Target $SourceHashes -Name '_hook_io.py' -Path $HookIoModulePath
Add-SourceHash -Target $SourceHashes -Name 'capability_manifest_recovery.py' -Path $RecoveryModulePath
Add-SourceHash -Target $SourceHashes -Name 'capability-manifest-builder.ps1' -Path $InstalledBuilderPath
Add-SourceHash -Target $SourceHashes -Name 'authority-receipt.schema.json' -Path $AuthorityReceiptSchemaPath
Add-SourceHash -Target $SourceHashes -Name 'query-catalogue.ps1' -Path $CatalogueQueryPath
Add-SourceHash -Target $SourceHashes -Name 'routing-policy.yaml' -Path $PolicyPath
Add-SourceHash -Target $SourceHashes -Name 'routing-policy.schema.json' -Path $RoutingPolicySchemaPath
Add-SourceHash -Target $SourceHashes -Name 'active-capabilities.schema.json' -Path $ActiveCapabilitiesSchemaPath
Add-SourceHash -Target $SourceHashes -Name 'project-scope-map.json' -Path $ProjectScopeMapPath
Add-SourceHash -Target $SourceHashes -Name 'project-scope-map.schema.json' -Path $ProjectScopeMapSchemaPath
Add-SourceHash -Target $SourceHashes -Name 'route-decision.schema.json' -Path $RouteDecisionSchemaPath
Add-SourceHash -Target $SourceHashes -Name 'ensure-node-dependencies.ps1' -Path $DependencyGuardPath
Add-SourceHash -Target $SourceHashes -Name 'dependency-readiness.README.md' -Path $DependencyReadinessReadmePath -Required $false
Add-SourceHash -Target $SourceHashes -Name 'universal-skills-2026-07-25.csv' -Path $SkillsCsvPath
Add-SourceHash -Target $SourceHashes -Name 'universal-plugins-2026-07-25.csv' -Path $PluginsCsvPath
Add-SourceHash -Target $SourceHashes -Name 'universal-tool-families-and-mcps-2026-07-25.csv' -Path $ToolsCsvPath
$SourceHashes['live-mcp-list'] = Get-TextSha256 -Text $McpCanonical
$SourceHashes['live-plugin-list'] = Get-TextSha256 -Text $PluginCanonical
$SourceHashes['passive-skill-list'] = Get-TextSha256 -Text $SkillCanonical
$SourceHashes['plugin-cache-inventory'] = [string]$FinalRecoveryAuthority.plugin_cache_inventory_sha256

if ($GeneratedAt) {
    $GeneratedTimestamp = [DateTimeOffset]::Parse($GeneratedAt).ToUniversalTime()
}
else {
    $GeneratedTimestamp = [DateTimeOffset]::UtcNow
}
$Manifest = [ordered]@{
    schema_version = '1.2'
    generated_at = $GeneratedTimestamp.ToString('o')
    snapshot_id = "universal-capabilities-live-authority-$($GeneratedTimestamp.ToString('yyyy-MM-dd'))"
    freshness_status = 'fresh'
    authority_model = [ordered]@{
        activation = @('passive-current-skill-roots', 'live-plugin-list', 'live-mcp-list', 'live-config-component-state', 'existing-current-source', 'plugin-cache-inventory', 'authority-receipt-v2')
        metadata_only = @('universal-skills-2026-07-25.csv', 'universal-plugins-2026-07-25.csv', 'universal-tool-families-and-mcps-2026-07-25.csv')
        fail_closed = $true
    }
    config_fingerprint = [ordered]@{
        projection_schema = 'capability-config-v1'
        sha256 = $ConfigCapabilityHash
        raw_sha256 = $ConfigHash
    }
    authority_receipt = $FinalRecoveryAuthority
    source_hashes = $SourceHashes
    suppressed_capabilities = @($SuppressedCapabilities | Sort-Object id, reason_code)
    entries = @($Entries | Sort-Object kind, name)
}

$ManifestDirectory = Split-Path -Parent $ManifestPath
New-Item -ItemType Directory -Path $ManifestDirectory -Force | Out-Null
$Json = $Manifest | ConvertTo-Json -Depth 30
$TemporaryManifestPath = "$ManifestPath.tmp-$PID-$([Guid]::NewGuid().ToString('N'))"
try {
    [System.IO.File]::WriteAllText($TemporaryManifestPath, $Json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    $PreMoveRecoveryAuthority = Get-RecoveryAuthorityReceipt -PythonExe $ResolvedRouterPythonExe -ModulePath $RecoveryModulePath -CodexHomePath $CodexHome
    if ([string]$PreMoveRecoveryAuthority.snapshot_sha256 -ne [string]$FinalRecoveryAuthority.snapshot_sha256 -or
        ($ExpectedAuthoritySnapshotSha256 -and
         [string]$PreMoveRecoveryAuthority.snapshot_sha256 -ne $ExpectedAuthoritySnapshotSha256)) {
        throw 'Recovery authority changed after candidate serialization. Manifest replacement is denied.'
    }
    [System.IO.File]::Move($TemporaryManifestPath, $ManifestPath, $true)
}
finally {
    if (Test-Path -LiteralPath $TemporaryManifestPath) {
        Remove-Item -LiteralPath $TemporaryManifestPath -Force
    }
}

$Counts = $Entries | Group-Object kind | Sort-Object Name | ForEach-Object {
    [ordered]@{ kind = $_.Name; count = $_.Count }
}

[ordered]@{
    manifest_path = $ManifestPath
    manifest_sha256 = Get-RequiredSha256 -Path $ManifestPath -Label 'generated manifest'
    total = $Entries.Count
    counts = @($Counts)
    suppressed_count = $SuppressedCapabilities.Count
    source_hashes = $SourceHashes
} | ConvertTo-Json -Depth 12
