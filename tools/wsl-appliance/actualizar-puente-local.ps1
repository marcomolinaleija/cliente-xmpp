[CmdletBinding()]
param(
    [string]$DistroName = "WhatsAppCAN-Bridge",
    [string]$PackageUrl = "https://github.com/marcomolinaleija/cliente-xmpp/releases/download/wsl-appliance-v1.1.0/WhatsAppCAN-Bridge-amd64.wsl",
    [string]$PackageSha256 = "5c9efa86eeefdee1836bb822e277cc4c217b1317fc85f7d53d831929b13a8f74",
    [long]$PackageSizeBytes = 747847680,
    [string]$InstallerUrl = "https://raw.githubusercontent.com/marcomolinaleija/cliente-xmpp/wsl-appliance-v1.1.0/tools/wsl-appliance/install-appliance.ps1",
    [string]$InstallerSha256 = "c9af53fc4fe5f4018393fb8d46f5116967f3fcaebb75c521738195a68d508eec",
    [string]$DownloadDirectory = (Join-Path $env:LOCALAPPDATA "WhatsAppCAN\downloads\wsl-appliance-v1.1.0"),
    [switch]$Si,
    [switch]$ConservarDescargas
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Get-WslDistributionNames {
    $names = & wsl.exe --list --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo consultar la lista de distribuciones WSL. Comprueba que WSL esté instalado."
    }
    return @($names | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_ })
}

function Test-ModernAppliance {
    param([Parameter(Mandatory = $true)][string]$Distribution)

    & wsl.exe -d $Distribution -u root -- test -x /usr/local/libexec/whatsapp-can-bridge-image
    if ($LASTEXITCODE -ne 0) {
        return $false
    }
    & wsl.exe -d $Distribution -u root -- grep -Fq 'http_file_share' /opt/whatsapp-can-bridge/templates/prosody.cfg.lua.in
    return $LASTEXITCODE -eq 0
}

function Assert-DownloadUri {
    param(
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string[]]$AllowedHosts
    )

    $uri = [Uri]$Value
    if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne "https" -or $AllowedHosts -notcontains $uri.DnsSafeHost) {
        throw "La dirección de descarga no es una URL HTTPS aprobada de GitHub: $Value"
    }
    return $uri
}

function Test-FileHash {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    return $actual -eq $ExpectedSha256
}

function Receive-VerifiedFile {
    param(
        [Parameter(Mandatory = $true)][Uri]$Uri,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256,
        [Parameter(Mandatory = $true)][string]$Description
    )

    if (Test-FileHash -Path $Destination -ExpectedSha256 $ExpectedSha256) {
        Write-Host "$Description ya estaba descargado y su SHA-256 es correcto."
        return
    }
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Force
    }

    Write-Host "Descargando $Description..."
    try {
        Import-Module BitsTransfer -ErrorAction Stop
        Start-BitsTransfer -Source $Uri.AbsoluteUri -Destination $Destination -DisplayName "WhatsApp CAN: $Description" -Description "Actualización segura del puente local"
    }
    catch {
        Write-Warning "BITS no pudo completar la descarga; se reintentará mediante HTTPS."
        if (Test-Path -LiteralPath $Destination) {
            Remove-Item -LiteralPath $Destination -Force
        }
        Invoke-WebRequest -Uri $Uri.AbsoluteUri -OutFile $Destination -UseBasicParsing
    }

    if (-not (Test-FileHash -Path $Destination -ExpectedSha256 $ExpectedSha256)) {
        Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
        throw "El SHA-256 de $Description no coincide. La descarga se eliminó y no se modificó la distribución."
    }
    Write-Host "$Description descargado y verificado correctamente."
}

if ($PSVersionTable.PSVersion -lt [Version]"5.1") {
    throw "Se requiere Windows PowerShell 5.1 o posterior."
}
if ($DistroName -notmatch '^[A-Za-z0-9._-]{1,64}$') {
    throw "El nombre de la distribución contiene caracteres no permitidos."
}
$PackageSha256 = $PackageSha256.Trim().ToLowerInvariant()
$InstallerSha256 = $InstallerSha256.Trim().ToLowerInvariant()
if ($PackageSha256 -notmatch '^[0-9a-f]{64}$' -or $InstallerSha256 -notmatch '^[0-9a-f]{64}$') {
    throw "Los SHA-256 configurados no tienen un formato válido."
}
if ($PackageSizeBytes -le 0) {
    throw "El tamaño esperado del appliance no es válido."
}

$knownDistros = Get-WslDistributionNames
if ($knownDistros -notcontains $DistroName) {
    throw "No existe la distribución $DistroName. Este script sólo actualiza instalaciones locales anteriores."
}
if (Test-ModernAppliance -Distribution $DistroName) {
    Write-Host "La distribución $DistroName ya es compatible con actualizaciones del puente y envío de archivos."
    Write-Host "No se realizó ningún cambio."
    exit 0
}

Write-Host ""
Write-Host "Actualización del puente local de WhatsApp CAN"
Write-Host "--------------------------------------------"
Write-Host "Se detectó una distribución anterior: $DistroName."
Write-Host "Antes de reemplazarla se crearán dos respaldos con SHA-256."
Write-Host "Se conservarán la sesión de WhatsApp, certificados, adjuntos y datos de Prosody."
Write-Host "Si la nueva distribución no supera las pruebas, se restaurará automáticamente la anterior."
Write-Host "La descarga ocupa aproximadamente $([Math]::Ceiling($PackageSizeBytes / 1MB)) MiB."
Write-Host "No cierres esta ventana ni apagues el equipo durante el reemplazo."
Write-Host ""
if (-not $Si) {
    $confirmation = Read-Host "Escribe ACTUALIZAR para continuar"
    if ($confirmation -cne "ACTUALIZAR") {
        Write-Host "Actualización cancelada; no se realizó ningún cambio."
        exit 0
    }
}

$packageUri = Assert-DownloadUri -Value $PackageUrl -AllowedHosts @("github.com")
$installerUri = Assert-DownloadUri -Value $InstallerUrl -AllowedHosts @("raw.githubusercontent.com")
$resolvedDownloadDirectory = [IO.Path]::GetFullPath($DownloadDirectory)
New-Item -ItemType Directory -Path $resolvedDownloadDirectory -Force | Out-Null
$packagePath = Join-Path $resolvedDownloadDirectory "WhatsAppCAN-Bridge-amd64.wsl"
$installerPath = Join-Path $resolvedDownloadDirectory "install-appliance.ps1"

$migrationCompleted = $false
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Receive-VerifiedFile -Uri $packageUri -Destination $packagePath -ExpectedSha256 $PackageSha256 -Description "el appliance 1.1"
    if ((Get-Item -LiteralPath $packagePath).Length -ne $PackageSizeBytes) {
        throw "El tamaño del appliance no coincide con el publicado. No se modificó la distribución."
    }
    Receive-VerifiedFile -Uri $installerUri -Destination $installerPath -ExpectedSha256 $InstallerSha256 -Description "el migrador firmado por SHA-256"

    Write-Host "Iniciando la migración segura. Este paso puede tardar varios minutos..."
    $windowsPowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    & $windowsPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installerPath `
        -PackagePath $packagePath `
        -DistroName $DistroName `
        -ExpectedPackageSha256 $PackageSha256 `
        -InstallOrResume
    if ($LASTEXITCODE -ne 0) {
        throw "El migrador terminó con el código $LASTEXITCODE. Revisa el mensaje anterior; el respaldo permite recuperar la instalación."
    }
    $migrationCompleted = $true
    Write-Host ""
    Write-Host "Actualización completada correctamente."
    Write-Host "Tu sesión, certificados, conversaciones locales y adjuntos se conservaron."
    Write-Host "A partir de ahora el puente podrá actualizarse sin reemplazar toda la distribución."
}
finally {
    if ($migrationCompleted -and -not $ConservarDescargas) {
        foreach ($downloadedFile in $packagePath, $installerPath) {
            if (Test-Path -LiteralPath $downloadedFile -PathType Leaf) {
                Remove-Item -LiteralPath $downloadedFile -Force
            }
        }
        Write-Host "Las descargas temporales verificadas se eliminaron para liberar espacio."
    }
    elseif (-not $migrationCompleted) {
        Write-Host "Las descargas verificadas se conservaron en: $resolvedDownloadDirectory"
        Write-Host "Puedes ejecutar nuevamente este script para reintentar sin descargarlas otra vez."
    }
}
