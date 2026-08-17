[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackagePath,
    [string]$DistroName = "WhatsAppCAN-Bridge",
    [string]$InstallLocation = (Join-Path $env:LOCALAPPDATA "WhatsAppCAN\WSL"),
    [string]$ConnectionFile = (Join-Path $env:LOCALAPPDATA "WhatsAppCAN\bridge-connection.json"),
    [string]$CaCertificateFile = (Join-Path $env:LOCALAPPDATA "WhatsAppCAN\bridge-ca.crt"),
    [string]$MigrationBackupDirectory = (Join-Path $env:LOCALAPPDATA "WhatsAppCAN\migration-backups"),
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

function Protect-LocalFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    Invoke-Native icacls.exe @($Path, "/inheritance:r", "/grant:r", "${identity}:(F)")
}

function Write-ProtectedChecksum {
    param([Parameter(Mandatory = $true)][string]$Path)

    $hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    $checksumPath = "$Path.sha256"
    [IO.File]::WriteAllText(
        $checksumPath,
        "$hash  $([IO.Path]::GetFileName($Path))`n",
        [Text.UTF8Encoding]::new($false)
    )
    Protect-LocalFile -Path $Path
    Protect-LocalFile -Path $checksumPath
}

function Assert-BackupIntegrity {
    param([Parameter(Mandatory = $true)][string]$Path)

    $checksumPath = "$Path.sha256"
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf) -or
        -not (Test-Path -LiteralPath $checksumPath -PathType Leaf)) {
        throw "El respaldo de migración está incompleto: $Path"
    }
    $expected = ((Get-Content -LiteralPath $checksumPath -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($expected -notmatch '^[0-9a-f]{64}$' -or $actual -ne $expected) {
        throw "El respaldo de migración no coincide con su SHA-256: $Path"
    }
}

function Convert-ToWslPath {
    param(
        [Parameter(Mandatory = $true)][string]$Distribution,
        [Parameter(Mandatory = $true)][string]$WindowsPath
    )

    $null = $Distribution
    $fullPath = [IO.Path]::GetFullPath($WindowsPath)
    if ($fullPath -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "La ruta de respaldo debe estar en una unidad local de Windows."
    }
    $drive = $Matches[1].ToLowerInvariant()
    $relative = $Matches[2] -replace '\\', '/'
    return "/mnt/$drive/$relative"
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

function Write-MigrationJournal {
    param(
        [Parameter(Mandatory = $true)][string]$JournalPath,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Journal
    )

    $temporary = "$JournalPath.tmp"
    [IO.File]::WriteAllText(
        $temporary,
        (($Journal | ConvertTo-Json -Compress) + "`n"),
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporary -Destination $JournalPath -Force
    Protect-LocalFile -Path $JournalPath
}

function Restore-LegacyBackup {
    param(
        [Parameter(Mandatory = $true)][string]$Distribution,
        [Parameter(Mandatory = $true)][string]$BackupPath,
        [Parameter(Mandatory = $true)][string]$RestoreLocation
    )

    Assert-BackupIntegrity -Path $BackupPath
    $known = Get-WslDistributionNames
    if ($known -contains $Distribution) {
        & wsl.exe --terminate $Distribution | Out-Null
        Invoke-Native wsl.exe @("--unregister", $Distribution)
    }
    if (Test-Path -LiteralPath $RestoreLocation) {
        $items = @(Get-ChildItem -LiteralPath $RestoreLocation -Force)
        if ($items.Count -gt 0) {
            throw "La carpeta de rollback no está vacía: $RestoreLocation"
        }
    }
    else {
        New-Item -ItemType Directory -Path $RestoreLocation -Force | Out-Null
    }
    Invoke-Native wsl.exe @("--import", $Distribution, $RestoreLocation, $BackupPath, "--version", "2")
    Invoke-Native wsl.exe @(
        "-d", $Distribution, "-u", "root", "--",
        "/usr/local/sbin/whatsapp-can-bridge", "start"
    )
}

function Recover-InterruptedMigration {
    param(
        [Parameter(Mandatory = $true)][string]$JournalPath,
        [Parameter(Mandatory = $true)][string]$Distribution,
        [Parameter(Mandatory = $true)][string]$FallbackInstallLocation
    )

    if (-not (Test-Path -LiteralPath $JournalPath -PathType Leaf)) {
        return
    }
    $journal = Get-Content -LiteralPath $JournalPath -Raw | ConvertFrom-Json
    if ([string]$journal.distro_name -cne $Distribution) {
        throw "Existe una migración pendiente para otra distribución: $($journal.distro_name)"
    }
    if ([string]$journal.phase -eq "backup_ready" -and
        (Get-WslDistributionNames) -contains $Distribution) {
        Invoke-Native wsl.exe @(
            "-d", $Distribution, "-u", "root", "--",
            "/usr/local/sbin/whatsapp-can-bridge", "start"
        )
        Remove-Item -LiteralPath $JournalPath -Force
        return
    }

    $backupPath = [IO.Path]::GetFullPath([string]$journal.full_backup)
    $backupRoot = [IO.Path]::GetFullPath($MigrationBackupDirectory).TrimEnd('\') + '\'
    if (-not $backupPath.StartsWith($backupRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "El journal de migración apunta fuera de la carpeta de respaldos."
    }
    $restoreLocation = "$([IO.Path]::GetFullPath($FallbackInstallLocation))-rollback-$([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))"
    $restoreParameters = @{
        Distribution = $Distribution
        BackupPath = $backupPath
        RestoreLocation = $restoreLocation
    }
    Restore-LegacyBackup @restoreParameters
    Remove-Item -LiteralPath $JournalPath -Force
    Write-Host "Se restauró automáticamente la distribución anterior tras una migración interrumpida."
}

function Invoke-LegacyMigration {
    param(
        [Parameter(Mandatory = $true)][string]$Distribution,
        [Parameter(Mandatory = $true)][string]$NewPackage,
        [Parameter(Mandatory = $true)][string]$BaseInstallLocation,
        [Parameter(Mandatory = $true)][string]$BackupDirectory,
        [Parameter(Mandatory = $true)][string]$JournalPath
    )

    $stamp = [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss")
    $backupBase = Join-Path $BackupDirectory "$Distribution-before-1.1-$stamp"
    $stateBackup = "$backupBase-state.tar.gz"
    $fullBackup = "$backupBase-full.tar.gz"
    $targetInstallLocation = "$([IO.Path]::GetFullPath($BaseInstallLocation))-upgrade-$stamp"
    $oldWasUnregistered = $false
    $newWasInstalled = $false
    $journal = [ordered]@{
        schema_version = 1
        distro_name = $Distribution
        phase = "creating_backups"
        full_backup = $fullBackup
        state_backup = $stateBackup
        target_location = $targetInstallLocation
        created_at = [DateTime]::UtcNow.ToString("o")
    }

    New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
    Write-Host "La distribución instalada es anterior a 1.1; se migrará conservando la sesión."
    try {
        Invoke-Native wsl.exe @(
            "-d", $Distribution, "-u", "root", "--",
            "/usr/local/sbin/whatsapp-can-bridge", "stop"
        )
        $stateWslPath = Convert-ToWslPath -Distribution $Distribution -WindowsPath $stateBackup
        Invoke-Native wsl.exe @(
            "-d", $Distribution, "-u", "root", "--", "tar",
            "--ignore-failed-read", "--numeric-owner", "--exclude=var/lib/prosody/prosody.sock",
            "-C", "/", "-czf", $stateWslPath,
            "etc/whatsapp-can-bridge/credentials",
            "etc/whatsapp-can-bridge/tls",
            "var/lib/whatsapp-can-bridge/slidge",
            "var/lib/whatsapp-can-bridge/attachments",
            "var/lib/prosody"
        )
        Write-ProtectedChecksum -Path $stateBackup
        Invoke-Native wsl.exe @("--terminate", $Distribution)
        Invoke-Native wsl.exe @("--export", $Distribution, $fullBackup, "--format", "tar.gz")
        Write-ProtectedChecksum -Path $fullBackup
        Assert-BackupIntegrity -Path $stateBackup
        Assert-BackupIntegrity -Path $fullBackup

        $journal["phase"] = "backup_ready"
        Write-MigrationJournal -JournalPath $JournalPath -Journal $journal
        foreach ($requiredPort in 5222, 5280, 8080) {
            if (Test-LocalTcpPort -Port $requiredPort) {
                throw "El puerto local $requiredPort sigue ocupado después de detener el puente anterior."
            }
        }

        $journal["phase"] = "replacing"
        Write-MigrationJournal -JournalPath $JournalPath -Journal $journal
        Invoke-Native wsl.exe @("--unregister", $Distribution)
        $oldWasUnregistered = $true
        if (Test-Path -LiteralPath $targetInstallLocation) {
            throw "La carpeta temporal de la nueva distribución ya existe: $targetInstallLocation"
        }
        New-Item -ItemType Directory -Path $targetInstallLocation -Force | Out-Null
        Invoke-Native wsl.exe @(
            "--install", "--from-file", $NewPackage, "--name", $Distribution,
            "--location", $targetInstallLocation, "--no-launch"
        )
        $newWasInstalled = $true
        $journal["phase"] = "restoring_state"
        Write-MigrationJournal -JournalPath $JournalPath -Journal $journal

        & wsl.exe -d $Distribution -u root -- systemctl stop whatsapp-can-slidge.service nginx.service prosody.service | Out-Null
        $stateWslPath = Convert-ToWslPath -Distribution $Distribution -WindowsPath $stateBackup
        Invoke-Native wsl.exe @(
            "-d", $Distribution, "-u", "root", "--", "tar",
            "--numeric-owner", "-C", "/", "-xzf", $stateWslPath
        )
        $journal["phase"] = "validating"
        Write-MigrationJournal -JournalPath $JournalPath -Journal $journal
        Invoke-Native wsl.exe @(
            "-d", $Distribution, "-u", "root", "--",
            "/usr/local/sbin/whatsapp-can-bridge", "configure"
        )
        if (-not (Test-ModernAppliance -Distribution $Distribution)) {
            throw "La distribución migrada no contiene el actualizador y XEP-0363 esperados."
        }
        Invoke-Native wsl.exe @(
            "-d", $Distribution, "-u", "root", "--",
            "/usr/local/sbin/whatsapp-can-bridge", "smoke"
        )
        Remove-Item -LiteralPath $JournalPath -Force
        Write-Host "Migración a la distribución 1.1 completada; la sesión y los datos se conservaron."
        Write-Host "Respaldo completo conservado en: $fullBackup"
    }
    catch {
        $migrationError = $_
        if (-not $oldWasUnregistered) {
            try {
                Invoke-Native wsl.exe @(
                    "-d", $Distribution, "-u", "root", "--",
                    "/usr/local/sbin/whatsapp-can-bridge", "start"
                )
            }
            catch {
                Write-Warning "No se pudo reiniciar la distribución anterior después del fallo previo al reemplazo."
            }
        }
        else {
            try {
                if ($newWasInstalled -and (Get-WslDistributionNames) -contains $Distribution) {
                    & wsl.exe --terminate $Distribution | Out-Null
                    Invoke-Native wsl.exe @("--unregister", $Distribution)
                }
                $rollbackLocation = "$([IO.Path]::GetFullPath($BaseInstallLocation))-rollback-$stamp"
                $rollbackParameters = @{
                    Distribution = $Distribution
                    BackupPath = $fullBackup
                    RestoreLocation = $rollbackLocation
                }
                Restore-LegacyBackup @rollbackParameters
                Remove-Item -LiteralPath $JournalPath -Force -ErrorAction SilentlyContinue
                Write-Warning "La migración falló y se restauró automáticamente la distribución anterior."
            }
            catch {
                Write-Warning "El rollback automático no terminó. El respaldo recuperable está en: $fullBackup"
            }
        }
        throw $migrationError
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
if ($DistroName -notmatch '^[A-Za-z0-9._-]{1,64}$') {
    throw "El nombre de la distribución contiene caracteres no permitidos."
}
$resolvedMigrationBackupDirectory = [IO.Path]::GetFullPath($MigrationBackupDirectory)
New-Item -ItemType Directory -Path $resolvedMigrationBackupDirectory -Force | Out-Null
$migrationJournal = Join-Path $resolvedMigrationBackupDirectory "legacy-upgrade-journal.json"
$recoveryParameters = @{
    JournalPath = $migrationJournal
    Distribution = $DistroName
    FallbackInstallLocation = $InstallLocation
}
Recover-InterruptedMigration @recoveryParameters

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

if ($distroExists -and -not (Test-ModernAppliance -Distribution $DistroName)) {
    if (-not $InstallOrResume) {
        throw "La distribución instalada necesita migrarse; vuelve a ejecutar con -InstallOrResume."
    }
    $migrationParameters = @{
        Distribution = $DistroName
        NewPackage = $resolvedPackage
        BaseInstallLocation = $InstallLocation
        BackupDirectory = $resolvedMigrationBackupDirectory
        JournalPath = $migrationJournal
    }
    Invoke-LegacyMigration @migrationParameters
    $distroExists = $true
}

if (-not $distroExists) {
    foreach ($requiredPort in 5222, 5280, 8080) {
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
