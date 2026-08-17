[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SourcePath,
    [Parameter(Mandatory = $true)][string]$DestinationPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$resolvedSource = (Resolve-Path -LiteralPath $SourcePath).Path
$sourceBytes = [IO.File]::ReadAllBytes($resolvedSource)
if ($sourceBytes.Length -lt 4 -or
    $sourceBytes[0] -ne 0xEF -or
    $sourceBytes[1] -ne 0xBB -or
    $sourceBytes[2] -ne 0xBF) {
    throw "El actualizador fuente debe usar UTF-8 con BOM."
}

$strictUtf8 = [Text.UTF8Encoding]::new($false, $true)
$sourceText = $strictUtf8.GetString($sourceBytes, 3, $sourceBytes.Length - 3)
$payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($sourceText))
$wrapper = @"
`$codigoActualizador = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$payload'))
& ([ScriptBlock]::Create(`$codigoActualizador)) @args
"@

if ($wrapper.ToCharArray() | Where-Object { [int]$_ -gt 127 }) {
    throw "El envoltorio público contiene caracteres que no son ASCII."
}

$resolvedDestination = [IO.Path]::GetFullPath($DestinationPath)
$destinationDirectory = Split-Path -Parent $resolvedDestination
New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
[IO.File]::WriteAllText(
    $resolvedDestination,
    "$wrapper`n",
    [Text.ASCIIEncoding]::new()
)
Write-Host "Actualizador público compatible con irm | iex generado en: $resolvedDestination"
