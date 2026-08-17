[CmdletBinding()]
param(
    [Parameter(Position = 0)][ValidateSet("status", "start", "stop", "restart", "logs", "smoke", "update", "connection", "backup", "uninstall")]
    [string]$Action = "status",
    [string]$DistroName = "WhatsAppCAN-Bridge",
    [string]$BackupPath,
    [string]$ConfirmDistroName,
    [string]$ManifestUrl
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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

function Protect-BackupFiles {
    param([Parameter(Mandatory = $true)][string]$BackupFile)

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    Invoke-Native icacls.exe @($BackupFile, "/inheritance:r", "/grant:r", "${identity}:(F)")
    Invoke-Native icacls.exe @("$BackupFile.sha256", "/inheritance:r", "/grant:r", "${identity}:(F)")
}

function Stop-ApplianceForExport {
    Invoke-Native wsl.exe @(
        "-d", $DistroName, "-u", "root", "--",
        "/usr/local/sbin/whatsapp-can-bridge", "stop"
    )
    Invoke-Native wsl.exe @("--terminate", $DistroName)
}

$knownDistros = @(& wsl.exe --list --quiet | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_ })
if ($LASTEXITCODE -ne 0 -or $knownDistros -notcontains $DistroName) {
    throw "No existe la distribución WSL $DistroName."
}

switch ($Action) {
    "backup" {
        if (-not $BackupPath) {
            throw "La acción backup requiere -BackupPath."
        }
        $resolvedBackup = [IO.Path]::GetFullPath($BackupPath)
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $resolvedBackup) | Out-Null
        Stop-ApplianceForExport
        Invoke-Native wsl.exe @("--export", $DistroName, $resolvedBackup, "--format", "tar.gz")
        $hash = (Get-FileHash -LiteralPath $resolvedBackup -Algorithm SHA256).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText("$resolvedBackup.sha256", "$hash  $([IO.Path]::GetFileName($resolvedBackup))`n", [Text.UTF8Encoding]::new($false))
        Protect-BackupFiles -BackupFile $resolvedBackup
        Write-Host "Respaldo creado: $resolvedBackup"
    }
    "uninstall" {
        if ($ConfirmDistroName -cne $DistroName) {
            throw "La desinstalación exige -ConfirmDistroName con el nombre exacto de la distribución."
        }
        if (-not $BackupPath) {
            throw "La desinstalación exige crear antes un respaldo mediante -BackupPath."
        }
        $resolvedBackup = [IO.Path]::GetFullPath($BackupPath)
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $resolvedBackup) | Out-Null
        Stop-ApplianceForExport
        Invoke-Native wsl.exe @("--export", $DistroName, $resolvedBackup, "--format", "tar.gz")
        $hash = (Get-FileHash -LiteralPath $resolvedBackup -Algorithm SHA256).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText("$resolvedBackup.sha256", "$hash  $([IO.Path]::GetFileName($resolvedBackup))`n", [Text.UTF8Encoding]::new($false))
        Protect-BackupFiles -BackupFile $resolvedBackup
        Invoke-Native wsl.exe @("--unregister", $DistroName)
        Write-Host "Distribución eliminada. El respaldo recuperable está en: $resolvedBackup"
    }
    "connection" {
        Invoke-Native wsl.exe @("-d", $DistroName, "-u", "root", "--", "/usr/local/sbin/whatsapp-can-bridge", "connection")
    }
    "update" {
        $arguments = @(
            "-d", $DistroName, "-u", "root", "--",
            "/usr/local/sbin/whatsapp-can-bridge", "update"
        )
        if ($ManifestUrl) {
            $manifestUri = [Uri]$ManifestUrl
            if (-not $manifestUri.IsAbsoluteUri -or $manifestUri.Scheme -ne "https") {
                throw "El manifiesto de actualización debe usar una URL HTTPS absoluta."
            }
            $arguments += @("--manifest-url", $manifestUri.AbsoluteUri)
        }
        Invoke-Native wsl.exe $arguments
    }
    default {
        Invoke-Native wsl.exe @("-d", $DistroName, "-u", "root", "--", "/usr/local/sbin/whatsapp-can-bridge", $Action)
    }
}
