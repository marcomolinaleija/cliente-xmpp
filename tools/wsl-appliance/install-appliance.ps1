[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackagePath,
    [string]$DistroName = "WhatsAppCAN-Bridge",
    [string]$InstallLocation = (Join-Path $env:LOCALAPPDATA "WhatsAppCAN\WSL"),
    [string]$ConnectionFile = (Join-Path $env:LOCALAPPDATA "WhatsAppCAN\bridge-connection.json"),
    [string]$CaCertificateFile = (Join-Path $env:LOCALAPPDATA "WhatsAppCAN\bridge-ca.crt"),
    [string]$ExpectedPackageSha256 = "",
    [switch]$Resume,
    [switch]$InstallOrResume
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$minimumWslVersion = [Version]"2.4.4.0"

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

function Get-WslVersion {
    $text = ((& wsl.exe --version | Out-String) -replace "`0", "")
    if ($LASTEXITCODE -ne 0 -or $text -notmatch '(\d+\.\d+\.\d+\.\d+)') {
        throw "WSL no está instalado o no expone una versión compatible."
    }
    return [Version]$Matches[1]
}

function Get-WslDistributionNames {
    $names = & wsl.exe --list --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo consultar la lista de distribuciones WSL."
    }
    return @($names | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_ })
}

function Test-LocalTcpPort {
    param([Parameter(Mandatory = $true)][int]$Port)

    $client = [Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.ConnectAsync("127.0.0.1", $Port)
        if (-not $connection.Wait(500)) {
            return $false
        }
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

$resolvedPackage = (Resolve-Path -LiteralPath $PackagePath).Path
if ([IO.Path]::GetExtension($resolvedPackage) -ne ".wsl") {
    throw "El paquete debe tener extensión .wsl."
}

$wslVersion = Get-WslVersion
if ($wslVersion -lt $minimumWslVersion) {
    throw "Se requiere WSL $minimumWslVersion o posterior; está instalada la versión $wslVersion."
}
$distroExists = (Get-WslDistributionNames) -contains $DistroName
if ($distroExists -and -not $Resume -and -not $InstallOrResume) {
    throw "Ya existe una distribución llamada $DistroName. Usa -Resume sólo para completar esa instalación."
}

$checksumPath = "$resolvedPackage.sha256"
$expectedHash = $ExpectedPackageSha256.Trim().ToLowerInvariant()
if ($expectedHash -and $expectedHash -notmatch '^[0-9a-f]{64}$') {
    throw "El SHA-256 esperado del paquete no tiene un formato válido."
}
if (-not $expectedHash -and (Test-Path -LiteralPath $checksumPath -PathType Leaf)) {
    $expectedHash = ((Get-Content -LiteralPath $checksumPath -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
}
if ($expectedHash) {
    $actualHash = (Get-FileHash -LiteralPath $resolvedPackage -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "El paquete .wsl no coincide con el SHA-256 esperado."
    }
}

if (-not $distroExists) {
    foreach ($requiredPort in 5222, 8080) {
        if (Test-LocalTcpPort -Port $requiredPort) {
            throw "El puerto local $requiredPort ya está en uso. Detén la otra instancia o servicio antes de instalar el puente."
        }
    }

    $resolvedInstallParent = [IO.Path]::GetFullPath((Split-Path -Parent $InstallLocation))
    New-Item -ItemType Directory -Force -Path $resolvedInstallParent | Out-Null
    if (Test-Path -LiteralPath $InstallLocation) {
        $existingItems = @(Get-ChildItem -LiteralPath $InstallLocation -Force)
        if ($existingItems.Count -gt 0) {
            throw "La carpeta de instalación no está vacía: $InstallLocation"
        }
    }
    else {
        New-Item -ItemType Directory -Path $InstallLocation | Out-Null
    }

    Write-Host "Instalando la distribución $DistroName..."
    Invoke-Native wsl.exe @("--install", "--from-file", $resolvedPackage, "--name", $DistroName, "--location", $InstallLocation, "--no-launch")
}
else {
    Write-Host "Reanudando la configuración de $DistroName sin reemplazar sus datos..."
}

try {
    Write-Host "Generando credenciales locales e iniciando servicios..."
    Invoke-Native wsl.exe @("-d", $DistroName, "-u", "root", "--", "/usr/local/sbin/whatsapp-can-bridge", "configure")

    $connectionJson = (& wsl.exe -d $DistroName -u root -- /usr/local/sbin/whatsapp-can-bridge connection --show-password | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $connectionJson) {
        throw "No se pudo obtener la configuración local del cliente."
    }
    $connection = $connectionJson | ConvertFrom-Json
    $caCertificate = (& wsl.exe -d $DistroName -u root -- /usr/local/sbin/whatsapp-can-bridge ca-certificate | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $caCertificate -notmatch 'BEGIN CERTIFICATE') {
        throw "No se pudo exportar la CA local del appliance."
    }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $caDirectory = Split-Path -Parent $CaCertificateFile
    New-Item -ItemType Directory -Force -Path $caDirectory | Out-Null
    if (Test-Path -LiteralPath $CaCertificateFile) {
        Invoke-Native icacls.exe @($CaCertificateFile, "/grant:r", "${identity}:(F)")
    }
    [IO.File]::WriteAllText($CaCertificateFile, "$caCertificate`n", [Text.UTF8Encoding]::new($false))
    $connection | Add-Member -NotePropertyName ca_file -NotePropertyValue ([IO.Path]::GetFullPath($CaCertificateFile)) -Force
    $connectionJson = $connection | ConvertTo-Json -Compress

    $connectionDirectory = Split-Path -Parent $ConnectionFile
    New-Item -ItemType Directory -Force -Path $connectionDirectory | Out-Null
    [IO.File]::WriteAllText($ConnectionFile, "$connectionJson`n", [Text.UTF8Encoding]::new($false))

    Invoke-Native icacls.exe @($ConnectionFile, "/inheritance:r", "/grant:r", "${identity}:(F)")
    Invoke-Native icacls.exe @($CaCertificateFile, "/inheritance:r", "/grant:r", "${identity}:(R)")

    Write-Host "Instalación completada."
    Write-Host "Credenciales locales protegidas en: $ConnectionFile"
    Write-Host "CA local fijada en: $CaCertificateFile"
    Invoke-Native wsl.exe @("-d", $DistroName, "-u", "root", "--", "/usr/local/sbin/whatsapp-can-bridge", "status")
}
catch {
    Write-Warning "La distribución quedó instalada para diagnóstico; no se eliminó porque puede contener información útil."
    throw
}
