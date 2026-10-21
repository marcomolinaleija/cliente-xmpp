[CmdletBinding()]
param(
    [switch]$Yes,
    [string]$Repository = "marcomolinaleija/cliente-xmpp",
    [string]$ManifestPath = "",
    [string]$ArtifactDirectory = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ManifestPath = if ($ManifestPath) {
    $ManifestPath
}
else {
    Join-Path $PSScriptRoot "release-manifest.json"
}
$ArtifactDirectory = if ($ArtifactDirectory) {
    $ArtifactDirectory
}
else {
    Join-Path $PSScriptRoot "..\..\dist\wsl"
}
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $projectRoot

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "El comando '$FilePath $($Arguments -join ' ')' terminó con código $LASTEXITCODE."
    }
}

$manifest = Get-Content -LiteralPath (Resolve-Path $ManifestPath) -Raw | ConvertFrom-Json
if ($manifest.schema_version -ne 1) {
    throw "Versión de manifiesto no compatible: $($manifest.schema_version)."
}
$tag = [string]$manifest.release_tag
$assetName = [string]$manifest.asset_name
$updaterAssetName = [string]$manifest.updater_asset_name
$expectedHash = ([string]$manifest.sha256).Trim().ToLowerInvariant()
$expectedSize = [long]$manifest.size_bytes
if ($tag -notmatch '^wsl-appliance-v\d+\.\d+\.\d+$') {
    throw "El tag del appliance no tiene el formato esperado: $tag."
}
if ($assetName -notmatch '^[A-Za-z0-9._-]+\.wsl$') {
    throw "El nombre del asset WSL no es válido: $assetName."
}
if ($updaterAssetName -notmatch '^[A-Za-z0-9._-]+\.ps1$') {
    throw "El nombre del actualizador no es válido: $updaterAssetName."
}
if ($expectedHash -notmatch '^[0-9a-f]{64}$' -or $expectedSize -le 0) {
    throw "El hash o el tamaño del manifiesto no son válidos."
}

$artifactRoot = (Resolve-Path $ArtifactDirectory).Path
$artifactPath = Join-Path $artifactRoot $assetName
$checksumPath = "$artifactPath.sha256"
$updaterSourcePath = Join-Path $PSScriptRoot $updaterAssetName
$updaterBuilderPath = Join-Path $PSScriptRoot "build-public-updater.ps1"
$updaterPath = Join-Path $artifactRoot $updaterAssetName
$updaterChecksumPath = Join-Path $artifactRoot "$updaterAssetName.sha256"
$notesPath = Join-Path $PSScriptRoot "release-notes.md"
$installerPath = Join-Path $artifactRoot 'install-appliance.ps1'
$installerChecksumPath = "$installerPath.sha256"
foreach ($requiredFile in $artifactPath, $checksumPath, $updaterSourcePath, $updaterBuilderPath, $notesPath) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Falta el archivo requerido: $requiredFile"
    }
}
if (-not $manifest.PSObject.Properties['installer_sha256'] -or
    -not $manifest.PSObject.Properties['installer_url']) {
    throw 'Prepara un manifiesto nuevo con prepare-appliance-release.ps1 antes de publicar.'
}
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $installerChecksumPath -PathType Leaf)) {
    throw 'Falta el instalador verificado de la appliance.'
}
$installerHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
$sourceInstallerText = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'install-appliance.ps1') -Raw -Encoding UTF8
$sourceInstallerText = $sourceInstallerText.Replace("`r`n", "`n").Replace("`n", "`r`n")
$sourceInstallerBytes = [byte[]](@(0xEF, 0xBB, 0xBF) + [Text.Encoding]::UTF8.GetBytes($sourceInstallerText))
$sourceHasher = [Security.Cryptography.SHA256]::Create()
try { $sourceInstallerHash = ([BitConverter]::ToString($sourceHasher.ComputeHash($sourceInstallerBytes))).Replace('-', '').ToLowerInvariant() }
finally { $sourceHasher.Dispose() }
if ($sourceInstallerHash -ne $installerHash) { throw 'El instalador preparado no coincide con las fuentes actuales.' }
$installerChecksum = ((Get-Content -LiteralPath $installerChecksumPath -Raw).Trim() -split '\s+')[0]
$expectedInstallerUrl = "https://github.com/$Repository/releases/download/$tag/install-appliance.ps1"
if ($installerHash -ne [string]$manifest.installer_sha256 -or $installerChecksum -ne $installerHash -or
    [string]$manifest.installer_url -cne $expectedInstallerUrl) {
    throw 'El instalador o su URL no coincide con el manifiesto candidato.'
}
$packageVersionJson = (& tar.exe -xOf $artifactPath --include 'opt/whatsapp-can-bridge/version.json' --include './opt/whatsapp-can-bridge/version.json' | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]($packageVersionJson | ConvertFrom-Json).appliance_version -ne [string]$manifest.appliance_version -or
    $tag -cne "wsl-appliance-v$($manifest.appliance_version)") {
    throw 'La versión del paquete no coincide con el manifiesto o tag.'
}

$artifact = Get-Item -LiteralPath $artifactPath
$packageCapabilities = $packageVersionJson | ConvertFrom-Json
if ([Version]$manifest.appliance_version -ge [Version]'1.2.0' -and
    (-not $packageCapabilities.PSObject.Properties['bridge_voice'] -or $packageCapabilities.bridge_voice -ne $true -or
     -not $packageCapabilities.PSObject.Properties['voice_required'] -or $packageCapabilities.voice_required -ne $true -or
     -not $packageCapabilities.PSObject.Properties['voice_configuration'] -or [int]$packageCapabilities.voice_configuration -lt 2)) {
    throw 'No se puede publicar una appliance nueva sin motor de voz y activación obligatoria.'
}
$actualHash = (Get-FileHash -LiteralPath $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumHash = ((Get-Content -LiteralPath $checksumPath -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
if ($artifact.Length -ne $expectedSize) {
    throw "El tamaño del appliance no coincide con release-manifest.json."
}
if ($actualHash -ne $expectedHash -or $checksumHash -ne $expectedHash) {
    throw "El appliance o su archivo .sha256 no coincide con release-manifest.json."
}
& $updaterBuilderPath -SourcePath $updaterSourcePath -DestinationPath $updaterPath
$updaterHash = (Get-FileHash -LiteralPath $updaterPath -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText(
    $updaterChecksumPath,
    "$updaterHash  $updaterAssetName`n",
    [Text.UTF8Encoding]::new($false)
)

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git no está disponible en PATH."
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI no está disponible en PATH."
}
if ((git status --porcelain | Out-String).Trim()) {
    throw "El árbol de trabajo debe estar limpio antes de publicar el appliance."
}
Invoke-Native git @("fetch", "origin", "--tags")
$branch = (git branch --show-current | Out-String).Trim()
if (-not $branch) {
    throw "No se puede publicar desde un HEAD separado."
}
$upstream = (git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $upstream) {
    throw "La rama $branch no tiene upstream configurado."
}
$aheadBehind = (git rev-list --left-right --count "$upstream...HEAD" | Out-String).Trim() -split '\s+'
if ($aheadBehind.Count -ne 2 -or $aheadBehind[0] -ne "0" -or $aheadBehind[1] -ne "0") {
    throw "La rama local debe estar sincronizada exactamente con $upstream."
}
if (git tag --list $tag) {
    throw "Ya existe el tag local $tag."
}
if ((git ls-remote --tags origin "refs/tags/$tag" | Out-String).Trim()) {
    throw "Ya existe el tag remoto $tag."
}
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& gh release view $tag --repo $Repository *> $null
$releaseViewExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorActionPreference
if ($releaseViewExitCode -eq 0) {
    throw "Ya existe la release $tag."
}
Invoke-Native gh @("auth", "status")

Write-Host "Se publicará el appliance local:"
Write-Host "  Tag:      $tag"
Write-Host "  Asset:    $artifactPath"
Write-Host "  SHA-256:  $actualHash"
Write-Host "  Tamaño:   $($artifact.Length) bytes"
Write-Host "  Migrador: $updaterPath"
Write-Host "  SHA-256:  $updaterHash"
if (-not $Yes) {
    $confirmation = Read-Host "Escribe PUBLICAR $tag para continuar"
    if ($confirmation -cne "PUBLICAR $tag") {
        throw "Publicación cancelada."
    }
}

Invoke-Native git @("tag", "-a", $tag, "-m", "WhatsApp CAN appliance WSL2 $($manifest.appliance_version)")
try {
    Invoke-Native git @("push", "origin", "refs/tags/$tag")
}
catch {
    git tag -d $tag *> $null
    throw
}

& gh release create $tag $artifactPath $checksumPath $updaterPath $updaterChecksumPath $installerPath $installerChecksumPath `
    --repo $Repository `
    --title "WhatsApp CAN - Appliance WSL2 $($manifest.appliance_version)" `
    --notes-file $notesPath `
    --latest=false
if ($LASTEXITCODE -ne 0) {
    throw "No se pudo crear la release. El tag remoto se conserva para diagnóstico."
}

$publishedAssets = @(gh release view $tag --repo $Repository --json assets --jq ".assets[].name")
if ($publishedAssets -notcontains $assetName -or
    $publishedAssets -notcontains "$assetName.sha256" -or
    $publishedAssets -notcontains $updaterAssetName -or
    $publishedAssets -notcontains "$updaterAssetName.sha256" -or
    $publishedAssets -notcontains 'install-appliance.ps1' -or
    $publishedAssets -notcontains 'install-appliance.ps1.sha256') {
    throw "La release se creó, pero no contiene todos los assets esperados."
}
$releaseUrl = (gh release view $tag --repo $Repository --json url --jq ".url" | Out-String).Trim()
Write-Host "Appliance publicado y verificado: $releaseUrl"
