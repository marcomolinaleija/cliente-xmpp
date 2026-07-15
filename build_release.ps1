param(
    [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path $PSScriptRoot).Path
Set-Location $projectRoot

$runningBuild = @(
    Get-CimInstance Win32_Process -Filter "Name='WhatsApp-CAN.exe'" |
        Where-Object { $_.ExecutablePath -like "$projectRoot\dist\WhatsApp-CAN\*" }
)
if ($runningBuild.Count -gt 0) {
    throw "Cierra la copia de WhatsApp CAN que se está ejecutando desde dist antes de compilar."
}

$version = (& python -c "from cliente_xmpp import __version__; print(__version__)").Trim()
if (-not $version) {
    throw "No se pudo leer la versión de cliente_xmpp."
}
$projectVersion = (& python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])").Trim()
if ($projectVersion -ne $version) {
    throw "Las versiones no coinciden: cliente_xmpp=$version, pyproject=$projectVersion."
}
$versionInfo = Get-Content -LiteralPath "windows_version_info.txt" -Raw
foreach ($field in @("FileVersion", "ProductVersion")) {
    if ($versionInfo -notmatch "StringStruct\(u'$field', u'$([regex]::Escape($version))'\)") {
        throw "windows_version_info.txt no contiene $field=$version."
    }
}
$versionParts = @($version.Split("."))
while ($versionParts.Count -lt 4) {
    $versionParts += "0"
}
$fixedVersion = $versionParts[0..3] -join ", "
foreach ($field in @("filevers", "prodvers")) {
    if ($versionInfo -notmatch "$field=\($([regex]::Escape($fixedVersion))\)") {
        throw "windows_version_info.txt no contiene $field=($fixedVersion)."
    }
}

if (-not $SkipChecks) {
    & python -m compileall cliente_xmpp tests update.py
    if ($LASTEXITCODE -ne 0) { throw "compileall falló." }
    & python -m ruff check .
    if ($LASTEXITCODE -ne 0) { throw "ruff falló." }
    & python -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) { throw "Los tests fallaron." }
}

& python -m PyInstaller --clean --noconfirm update.spec
if ($LASTEXITCODE -ne 0) { throw "No se pudo compilar update.exe." }

& python -m PyInstaller --clean --noconfirm WhatsApp-CAN.spec
if ($LASTEXITCODE -ne 0) { throw "No se pudo compilar WhatsApp CAN." }

$distDir = Join-Path $projectRoot "dist\WhatsApp-CAN"
$mainExe = Join-Path $distDir "WhatsApp-CAN.exe"
$updaterExe = Join-Path $distDir "update.exe"
Copy-Item -LiteralPath (Join-Path $projectRoot "dist\update.exe") -Destination $updaterExe -Force
if (-not (Test-Path -LiteralPath $mainExe -PathType Leaf)) {
    throw "Falta $mainExe."
}
if (-not (Test-Path -LiteralPath $updaterExe -PathType Leaf)) {
    throw "Falta $updaterExe."
}

$releaseDir = Join-Path $projectRoot "release"
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
$zipName = "WhatsApp-CAN-$version.zip"
$zipPath = Join-Path $releaseDir $zipName
$checksumPath = "$zipPath.sha256"
Remove-Item -LiteralPath $zipPath, $checksumPath -Force -ErrorAction SilentlyContinue
Compress-Archive -LiteralPath $distDir -DestinationPath $zipPath -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText($checksumPath, "$hash  $zipName`n", [Text.UTF8Encoding]::new($false))
& python tools\validate_release.py $zipPath $checksumPath
if ($LASTEXITCODE -ne 0) { throw "La validación del paquete final falló." }

$isccCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
$iscc = $isccCandidates | Select-Object -First 1
if (-not $iscc) {
    throw "No se encontro ISCC.exe de Inno Setup 6. Instala Inno Setup antes de compilar."
}

$installerName = "WhatsApp-CAN-$version-Setup.exe"
$installerPath = Join-Path $releaseDir $installerName
$installerChecksumPath = "$installerPath.sha256"
Remove-Item -LiteralPath $installerPath, $installerChecksumPath -Force -ErrorAction SilentlyContinue
& $iscc `
    "/DMyAppVersion=$version" `
    "/DSourceDir=$distDir" `
    "/DOutputDir=$releaseDir" `
    (Join-Path $projectRoot "installer\WhatsApp-CAN.iss")
if ($LASTEXITCODE -ne 0) { throw "No se pudo compilar el instalador con Inno Setup." }
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "Falta $installerPath."
}
$installerHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText(
    $installerChecksumPath,
    "$installerHash  $installerName`n",
    [Text.UTF8Encoding]::new($false)
)

Write-Host "Release preparada:"
Write-Host "  $zipPath"
Write-Host "  $checksumPath"
Write-Host "  $installerPath"
Write-Host "  $installerChecksumPath"
