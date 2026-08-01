$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$HelperPath = Join-Path $RepoRoot "scripts\json-schema-validation.ps1"
$SchemaPath = Join-Path $RepoRoot "pack.schema.json"
$ManifestPath = Join-Path $RepoRoot "pack.manifest.json"
$NegativeFixturePath = Join-Path $RepoRoot "tests\fixtures\pack.manifest.invalid-schema.json"

. $HelperPath

$ManifestErrors = @(
  Get-JsonSchemaValidationErrors -JsonPath $ManifestPath -SchemaPath $SchemaPath
)
if ($ManifestErrors.Count -gt 0) {
  throw "The live pack manifest failed schema validation: $($ManifestErrors -join ' | ')"
}

$FixtureErrors = @(
  Get-JsonSchemaValidationErrors -JsonPath $NegativeFixturePath -SchemaPath $SchemaPath
)
if ($FixtureErrors.Count -eq 0) {
  throw "The negative pack manifest fixture unexpectedly passed schema validation."
}

$TemporaryManifest = Join-Path ([System.IO.Path]::GetTempPath()) "ccos-pack-schema-$([guid]::NewGuid().ToString('N')).json"
try {
  $TypedTriggerFixture = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
  $TypedTriggerFixture.artifact_definitions[0].trigger = "legacy_string_trigger"
  [System.IO.File]::WriteAllText(
    $TemporaryManifest,
    ($TypedTriggerFixture | ConvertTo-Json -Depth 100),
    [System.Text.UTF8Encoding]::new($false)
  )
  $TypedTriggerErrors = @(
    Get-JsonSchemaValidationErrors -JsonPath $TemporaryManifest -SchemaPath $SchemaPath
  )
  if ($TypedTriggerErrors.Count -eq 0) {
    throw "A legacy string trigger unexpectedly passed JSON Schema validation."
  }

  $UnexpectedPropertyFixture = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
  $UnexpectedPropertyFixture.artifact_definitions[0] |
    Add-Member -NotePropertyName unexpected_property -NotePropertyValue $true
  [System.IO.File]::WriteAllText(
    $TemporaryManifest,
    ($UnexpectedPropertyFixture | ConvertTo-Json -Depth 100),
    [System.Text.UTF8Encoding]::new($false)
  )
  $UnexpectedPropertyErrors = @(
    Get-JsonSchemaValidationErrors -JsonPath $TemporaryManifest -SchemaPath $SchemaPath
  )
  if ($UnexpectedPropertyErrors.Count -eq 0) {
    throw "An undeclared artifact property unexpectedly passed JSON Schema validation."
  }
} finally {
  if (Test-Path -LiteralPath $TemporaryManifest -PathType Leaf) {
    Remove-Item -LiteralPath $TemporaryManifest -Force
  }
}

Write-Output "JSON Schema validation smoke test passed."
