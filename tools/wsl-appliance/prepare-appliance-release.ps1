[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackagePath,
    [Parameter(Mandatory = $true)][string]$OutputManifestPath,
    [string]$Repository = 'marcomolinaleija/cliente-xmpp'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$package = Get-Item -LiteralPath (Resolve-Path -LiteralPath $PackagePath)
$output = [IO.Path]::GetFullPath($OutputManifestPath)
if (Test-Path -LiteralPath $output) { throw 'El manifiesto de salida ya existe; no se reemplazará.' }
if ($package.Extension -ne '.wsl') { throw 'Se requiere un paquete .wsl construido.' }
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw 'Repositorio inválido.' }
$json = (& tar.exe -xOf $package.FullName --include 'opt/whatsapp-can-bridge/version.json' --include './opt/whatsapp-can-bridge/version.json' | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'No se pudo leer la versión del paquete.' }
$version = $json | ConvertFrom-Json
if ([string]$version.appliance_version -notmatch '^\d+\.\d+\.\d+$') { throw 'El paquete no declara una versión de entrega válida.' }
if ([Version]$version.appliance_version -ge [Version]'1.2.0' -and
    (-not $version.PSObject.Properties['bridge_voice'] -or $version.bridge_voice -ne $true -or
     -not $version.PSObject.Properties['voice_required'] -or $version.voice_required -ne $true -or
     -not $version.PSObject.Properties['voice_configuration'] -or [int]$version.voice_configuration -lt 2)) {
    throw 'La entrega requiere motor de voz incluido y activación obligatoria.'
}
$tag = "wsl-appliance-v$($version.appliance_version)"
$baseUrl = "https://github.com/$Repository/releases/download/$tag"
$installerPath = Join-Path $package.DirectoryName 'install-appliance.ps1'
if (Test-Path -LiteralPath $installerPath) { throw 'Ya existe el asset del instalador; utiliza un directorio de preparación nuevo.' }
# Materialize exact upload bytes; the manifest never guesses a future checksum.
$installer = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'install-appliance.ps1') -Raw -Encoding UTF8
$installer = $installer.Replace("`r`n", "`n").Replace("`n", "`r`n")
[IO.File]::WriteAllText($installerPath, $installer, [Text.UTF8Encoding]::new($true))
$packageHash = (Get-FileHash -LiteralPath $package.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$installerHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
foreach ($assetPath in @($package.FullName, $installerPath)) {
    $hash = (Get-FileHash -LiteralPath $assetPath -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText("$assetPath.sha256", "$hash  $([IO.Path]::GetFileName($assetPath))`n", [Text.UTF8Encoding]::new($false))
}
$manifest = [ordered]@{
    schema_version = 1
    appliance_version = [string]$version.appliance_version
    bridge_image = [string]$version.bridge_image
    release_tag = $tag
    asset_name = $package.Name
    download_url = "$baseUrl/$($package.Name)"
    sha256 = $packageHash
    size_bytes = $package.Length
    updater_asset_name = 'actualizar-puente-local.ps1'
    installer_url = "$baseUrl/install-appliance.ps1"
    installer_sha256 = $installerHash
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $output) | Out-Null
[IO.File]::WriteAllText($output, (($manifest | ConvertTo-Json) + "`n"), [Text.UTF8Encoding]::new($false))
Write-Host "Manifiesto candidato preparado: $output. No se publicó ni se cambió el canal estable."
