[CmdletBinding()]
param(
    [string]$DistroName = "WhatsAppCAN-Bridge",
    [string]$ManifestUrl = "https://raw.githubusercontent.com/marcomolinaleija/cliente-xmpp/main/tools/wsl-appliance/release-manifest.json",
    [string]$DownloadDirectory = "",
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
    param(
        [Parameter(Mandatory = $true)][string]$Distribution,
        [Parameter(Mandatory = $true)][Version]$TargetVersion
    )

    $json = (& wsl.exe -d $Distribution -u root -- cat /opt/whatsapp-can-bridge/version.json | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $json) { throw "No se pudo consultar la versión instalada." }
    $value = [string]($json | ConvertFrom-Json).appliance_version
    if ($value -notmatch '^\d+\.\d+\.\d+(-dev)?$') { throw "Versión instalada inválida: $value" }
    $installedVersion = [Version]($value -replace '-dev$', '')
    if ($installedVersion -gt $TargetVersion) {
        throw "El appliance instalado ($installedVersion) es posterior al canal ($TargetVersion); no se reemplazará."
    }
    if ($installedVersion -lt $TargetVersion) { return $false }

    & wsl.exe -d $Distribution -u root -- test -x /usr/local/libexec/whatsapp-can-bridge-image
    if ($LASTEXITCODE -ne 0) {
        return $false
    }
    & wsl.exe -d $Distribution -u root -- grep -Fq 'http_file_share' /opt/whatsapp-can-bridge/templates/prosody.cfg.lua.in
    if ($LASTEXITCODE -ne 0) { return $false }
    if ($TargetVersion -ge [Version]'1.2.0') {
        & wsl.exe -d $Distribution -u root -- test -x /usr/local/libexec/whatsapp-can-bridge-voice
        if ($LASTEXITCODE -ne 0) { return $false }
        & wsl.exe -d $Distribution -u root -- /usr/local/libexec/whatsapp-can-bridge-voice required
        return $LASTEXITCODE -eq 0
    }
    return $true
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
$manifestUri = Assert-DownloadUri -Value $ManifestUrl -AllowedHosts @('raw.githubusercontent.com', 'github.com')
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$manifestResponse = Invoke-WebRequest -Uri $manifestUri.AbsoluteUri -UseBasicParsing
$manifest = $manifestResponse.Content | ConvertFrom-Json
if ($manifest.schema_version -ne 1 -or [string]$manifest.appliance_version -notmatch '^\d+\.\d+\.\d+$') {
    throw 'El manifiesto de appliance no tiene un esquema o versión compatibles.'
}
$targetVersion = [Version]$manifest.appliance_version
$PackageUrl = [string]$manifest.download_url
$PackageSha256 = ([string]$manifest.sha256).Trim().ToLowerInvariant()
$PackageSizeBytes = [long]$manifest.size_bytes
$InstallerUrl = [string]$manifest.installer_url
$InstallerSha256 = ([string]$manifest.installer_sha256).Trim().ToLowerInvariant()
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
if (Test-ModernAppliance -Distribution $DistroName -TargetVersion $targetVersion) {
    Write-Host "La distribución $DistroName ya tiene la versión $targetVersion y sus capacidades."
    Write-Host "No se realizó ningún cambio."
    return
}

Write-Host ""
Write-Host "Actualización del puente local de WhatsApp CAN"
Write-Host "--------------------------------------------"
Write-Host "Se actualizará la distribución $DistroName a $targetVersion."
Write-Host "Antes de reemplazarla se crearán respaldos con SHA-256 de la distribución y los datos."
Write-Host "Se conservarán la sesión de WhatsApp, certificados, adjuntos y datos de Prosody."
Write-Host "Si la nueva distribución no supera las pruebas, se restaurará automáticamente la anterior."
Write-Host "La descarga ocupa aproximadamente $([Math]::Ceiling($PackageSizeBytes / 1MB)) MiB."
Write-Host "No cierres esta ventana ni apagues el equipo durante el reemplazo."
Write-Host ""
if (-not $Si) {
    $confirmation = Read-Host "Escribe ACTUALIZAR para continuar"
    if ($confirmation.Trim() -ine "ACTUALIZAR") {
        Write-Host "Actualización cancelada; no se realizó ningún cambio."
        return
    }
}

$packageUri = Assert-DownloadUri -Value $PackageUrl -AllowedHosts @("github.com")
$installerUri = Assert-DownloadUri -Value $InstallerUrl -AllowedHosts @("raw.githubusercontent.com", "github.com")
if (-not $DownloadDirectory) {
    $DownloadDirectory = Join-Path $env:LOCALAPPDATA "WhatsAppCAN\downloads\wsl-appliance-v$targetVersion"
}
$resolvedDownloadDirectory = [IO.Path]::GetFullPath($DownloadDirectory)
New-Item -ItemType Directory -Path $resolvedDownloadDirectory -Force | Out-Null
$packagePath = Join-Path $resolvedDownloadDirectory "WhatsAppCAN-Bridge-amd64.wsl"
$installerPath = Join-Path $resolvedDownloadDirectory "install-appliance.ps1"

$migrationCompleted = $false
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Receive-VerifiedFile -Uri $packageUri -Destination $packagePath -ExpectedSha256 $PackageSha256 -Description "el appliance $targetVersion"
    if ((Get-Item -LiteralPath $packagePath).Length -ne $PackageSizeBytes) {
        throw "El tamaño del appliance no coincide con el publicado. No se modificó la distribución."
    }
    Receive-VerifiedFile -Uri $installerUri -Destination $installerPath -ExpectedSha256 $InstallerSha256 -Description "el migrador firmado por SHA-256"

    Write-Host "Iniciando la migración segura. Este paso puede tardar varios minutos..."
    $windowsPowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $installerArguments = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', $installerPath, '-PackagePath', $packagePath, '-DistroName', $DistroName,
        '-ExpectedPackageSha256', $PackageSha256, '-InstallOrResume')
    if ($targetVersion -ge [Version]'1.2.0') {
        $installerArguments += @('-ExpectedApplianceVersion', $targetVersion.ToString())
    }
    $previousModulePath = $env:PSModulePath
    try {
        # A caller running PowerShell Core can omit Windows PowerShell's
        # built-in module directory. Its child still needs Get-FileHash, etc.
        $windowsModules = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules'
        $env:PSModulePath = "$windowsModules;$previousModulePath"
        & $windowsPowerShell @installerArguments
        $installerExitCode = $LASTEXITCODE
    }
    finally { $env:PSModulePath = $previousModulePath }
    if ($installerExitCode -ne 0) {
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
