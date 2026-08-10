[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\..\dist\wsl"),
    [string]$WorkDirectory = (Join-Path $env:LOCALAPPDATA "WhatsAppCAN\WslBuild"),
    [switch]$KeepBuilder,
    [switch]$SkipBridgeImage,
    [switch]$OverwriteOutput
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$baseImageName = "ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz"
$baseImageUrl = "https://cloud-images.ubuntu.com/wsl/releases/noble/current/$baseImageName"
$baseImageSha256 = "8251e27ffff381a4af5f41dcb94d867de3e0d9774a9241908ab34555d99315ea"
$builderPrefix = "WhatsAppCAN-Bridge-Builder-"
$builderName = $builderPrefix + ([Guid]::NewGuid().ToString("N").Substring(0, 8))
$builderDirectory = Join-Path $WorkDirectory $builderName
$baseImagePath = Join-Path $WorkDirectory $baseImageName
$overlayDirectory = Join-Path $PSScriptRoot "rootfs-overlay"
$outputPath = Join-Path $OutputDirectory "WhatsAppCAN-Bridge-amd64.wsl"
$builderRegistered = $false

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

function Get-WslDistributionNames {
    $names = & wsl.exe --list --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo consultar la lista de distribuciones WSL."
    }
    return @($names | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_ })
}

function ConvertTo-WslMountPath {
    param([Parameter(Mandatory = $true)][string]$WindowsPath)

    $resolved = [IO.Path]::GetFullPath($WindowsPath)
    $root = [IO.Path]::GetPathRoot($resolved)
    if (-not $root -or $root.Length -lt 2 -or $root[1] -ne ':') {
        throw "El build requiere que el overlay esté en una unidad local de Windows: $resolved"
    }
    $drive = [char]::ToLowerInvariant($root[0])
    $relative = $resolved.Substring($root.Length).Replace('\', '/')
    return "/mnt/$drive/$relative"
}

function Remove-Builder {
    if (-not $script:builderRegistered) {
        return
    }
    if (-not $script:builderName.StartsWith($script:builderPrefix, [StringComparison]::Ordinal)) {
        throw "Se rechazó limpiar una distribución cuyo nombre no pertenece al build temporal."
    }
    if ((Get-WslDistributionNames) -contains $script:builderName) {
        Invoke-Native wsl.exe @("--unregister", $script:builderName)
    }
    $script:builderRegistered = $false
}

try {
    if (-not (Test-Path -LiteralPath $overlayDirectory -PathType Container)) {
        throw "No existe el overlay del appliance: $overlayDirectory"
    }

    New-Item -ItemType Directory -Force -Path $WorkDirectory, $OutputDirectory | Out-Null
    if (-not (Test-Path -LiteralPath $baseImagePath -PathType Leaf)) {
        Write-Host "Descargando la raíz Ubuntu 24.04 para WSL..."
        Invoke-WebRequest -Uri $baseImageUrl -OutFile $baseImagePath -UseBasicParsing
    }

    $actualBaseHash = (Get-FileHash -LiteralPath $baseImagePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualBaseHash -ne $baseImageSha256) {
        throw "La raíz Ubuntu descargada no coincide con el SHA-256 fijado."
    }

    if ((Get-WslDistributionNames) -contains $builderName) {
        throw "La distribución temporal ya existe inesperadamente: $builderName"
    }
    if (Test-Path -LiteralPath $builderDirectory) {
        throw "La carpeta temporal ya existe inesperadamente: $builderDirectory"
    }

    New-Item -ItemType Directory -Force -Path $builderDirectory | Out-Null
    Write-Host "Importando la distribución temporal $builderName..."
    Invoke-Native wsl.exe @("--import", $builderName, $builderDirectory, $baseImagePath, "--version", "2")
    $builderRegistered = $true

    $overlayWslPath = ConvertTo-WslMountPath $overlayDirectory
    Invoke-Native wsl.exe @("-d", $builderName, "-u", "root", "--", "/bin/cp", "-a", "$overlayWslPath/.", "/")

    $provisionArguments = @("-d", $builderName, "-u", "root", "--", "/bin/bash", "/opt/whatsapp-can-bridge/build/provision-rootfs.sh")
    if ($SkipBridgeImage) {
        $provisionArguments += "--skip-bridge-image"
    }
    Write-Host "Instalando Prosody, nginx, Podman y el runtime del appliance..."
    Invoke-Native wsl.exe $provisionArguments

    Invoke-Native wsl.exe @("--terminate", $builderName)
    if (Test-Path -LiteralPath $outputPath) {
        if (-not $OverwriteOutput) {
            throw "Ya existe $outputPath. Usa -OverwriteOutput si deseas reemplazar este artefacto de build."
        }
        Remove-Item -LiteralPath $outputPath -Force
        if (Test-Path -LiteralPath "$outputPath.sha256") {
            Remove-Item -LiteralPath "$outputPath.sha256" -Force
        }
    }
    Write-Host "Exportando $outputPath..."
    Invoke-Native wsl.exe @("--export", $builderName, $outputPath, "--format", "tar.gz")

    $outputHash = (Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $checksumPath = "$outputPath.sha256"
    [IO.File]::WriteAllText(
        $checksumPath,
        "$outputHash  $([IO.Path]::GetFileName($outputPath))`n",
        [Text.UTF8Encoding]::new($false)
    )
    Write-Host "Appliance creado: $outputPath"
    Write-Host "SHA-256: $outputHash"
}
finally {
    if ($builderRegistered -and -not $KeepBuilder) {
        Remove-Builder
    }
    elseif ($builderRegistered) {
        Write-Warning "Se conservó la distribución temporal $builderName por solicitud."
    }
}
