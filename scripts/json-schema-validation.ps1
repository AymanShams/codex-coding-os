function Get-JsonSchemaValidationErrors {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)]
    [string]$JsonPath,

    [Parameter(Mandatory = $true)]
    [string]$SchemaPath
  )

  if ($PSVersionTable.PSVersion -lt [version]"7.4") {
    return @("PowerShell 7.4 or later is required for JSON Schema validation with Test-Json.")
  }

  if (-not (Test-Path -LiteralPath $JsonPath -PathType Leaf)) {
    return @("JSON document does not exist: $JsonPath")
  }

  if (-not (Test-Path -LiteralPath $SchemaPath -PathType Leaf)) {
    return @("JSON Schema does not exist: $SchemaPath")
  }

  $ValidationErrors = @()
  try {
    $IsValid = Test-Json `
      -LiteralPath $JsonPath `
      -SchemaFile $SchemaPath `
      -ErrorAction SilentlyContinue `
      -ErrorVariable +ValidationErrors
  } catch {
    return @("JSON Schema validation could not run: $($_.Exception.Message)")
  }

  if ($IsValid) {
    return @()
  }

  $Messages = @(
    $ValidationErrors |
      ForEach-Object { $_.Exception.Message } |
      Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
      Select-Object -Unique
  )
  if ($Messages.Count -eq 0) {
    return @("JSON document does not satisfy the schema.")
  }

  return $Messages
}
