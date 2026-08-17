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
`$directorioRegistro = Join-Path `$env:LOCALAPPDATA 'WhatsAppCAN\logs'
New-Item -ItemType Directory -Path `$directorioRegistro -Force | Out-Null
`$rutaRegistro = Join-Path `$directorioRegistro ("actualizacion-puente-{0}.log" -f [DateTime]::Now.ToString('yyyyMMdd-HHmmss'))
`$transcriptIniciado = `$false
`$errorActualizacion = `$null
try {
    try {
        Start-Transcript -Path `$rutaRegistro -Force | Out-Null
        `$transcriptIniciado = `$true
    }
    catch {
        Write-Warning 'No se pudo iniciar el registro de diagnostico.'
    }
    `$codigoActualizador = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$payload'))
    & ([ScriptBlock]::Create(`$codigoActualizador)) @args
}
catch {
    `$errorActualizacion = `$_
    Write-Host ("No se pudo completar la actualizacion: {0}" -f `$_.Exception.Message) -ForegroundColor Red
}
finally {
    if (`$transcriptIniciado) {
        Stop-Transcript | Out-Null
        Write-Host "Registro de diagnostico: `$rutaRegistro"
    }
}
if (`$null -ne `$errorActualizacion) {
    throw `$errorActualizacion
}
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
