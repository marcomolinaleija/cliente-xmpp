[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CandidatePackage,
    [Parameter(Mandatory = $true)][string]$ModernPackage,
    [Parameter(Mandatory = $true)][string]$LegacyPackage,
    [Parameter(Mandatory = $true)][string]$ModernPackageSha256,
    [Parameter(Mandatory = $true)][string]$LegacyPackageSha256,
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '../../.test-results/appliance-native'),
    [ValidateNotNullOrEmpty()][ValidateSet('clean', 'legacy', 'modern', 'rollback')]
    [string[]]$Scenarios = @('clean', 'legacy', 'modern', 'rollback'),
    [switch]$KeepDistributions
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (@($Scenarios | Select-Object -Unique).Count -ne $Scenarios.Count) { throw 'No se permiten escenarios duplicados.' }
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$candidate = (Resolve-Path -LiteralPath $CandidatePackage).Path
$modern = (Resolve-Path -LiteralPath $ModernPackage).Path
$legacy = (Resolve-Path -LiteralPath $LegacyPackage).Path
$candidateHash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
$candidateMetadataText = (& tar.exe -xOf $candidate --include 'opt/whatsapp-can-bridge/version.json' --include './opt/whatsapp-can-bridge/version.json' | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'No se pudo comprobar la configuración de la candidata.' }
$candidateMetadata = $candidateMetadataText | ConvertFrom-Json
$candidateHasVoice = $candidateMetadata.PSObject.Properties['bridge_voice'] -and $candidateMetadata.bridge_voice -eq $true
if (-not $candidateHasVoice -or -not $candidateMetadata.PSObject.Properties['voice_required'] -or
    $candidateMetadata.voice_required -ne $true) { throw 'La candidata debe incluir voz obligatoria.' }
$installerTokens = $null; $installerParseErrors = $null
$installerAst = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'install-appliance.ps1'), [ref]$installerTokens, [ref]$installerParseErrors)
if ($installerParseErrors.Count) { throw ($installerParseErrors | Out-String) }
foreach ($node in $installerAst.EndBlock.Statements) {
    if ($node -is [Management.Automation.Language.FunctionDefinitionAst]) { . ([ScriptBlock]::Create($node.Extent.Text)) }
}
$testId = [Guid]::NewGuid().ToString('N').Substring(0, 8)
$created = [Collections.Generic.List[string]]::new()
$results = [Collections.Generic.List[object]]::new()
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

function Invoke-Native {
    param([string]$FilePath, [string[]]$Arguments)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Falló una comprobación nativa: $FilePath (código $LASTEXITCODE)." }
}

function Assert-TestName {
    param([string]$Name)
    if ($Name -notmatch "^WhatsAppCAN-Validate-$testId-(clean|legacy|modern|rollback)$" -or
        -not $created.Contains($Name)) { throw 'Se rechazó operar fuera de las distribuciones de esta prueba.' }
}

function Get-TestSnapshot {
    param([string]$Name)
    Assert-TestName $Name
    $data = (& wsl.exe -d $Name -u root -- sha256sum /etc/whatsapp-can-bridge/credentials /etc/whatsapp-can-bridge/tls/ca.crt /var/lib/whatsapp-can-bridge/slidge/migration-fixture /var/lib/whatsapp-can-bridge/attachments/migration-fixture | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo comprobar el estado sintético de la prueba.' }
    $options = (& wsl.exe -d $Name -u root -- grep -e '^CAN_MIGRATION_FIXTURE=' -e '^SLIDGE_CONVERT_STICKERS=' /etc/whatsapp-can-bridge/environment | Sort-Object | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'No se pudieron comprobar las opciones sintéticas del entorno.' }
    return "$data`n$options"
}

function Invoke-LocalSmokes {
    param([string]$Name, [string]$Directory)
    Assert-TestName $Name
    Invoke-Native wsl.exe @('-d', $Name, '-u', 'root', '--', '/usr/local/sbin/whatsapp-can-bridge', 'smoke')
    $connectionPath = Join-Path $Directory 'connection.json'
    $runner = 'import runpy,sys; p=sys.argv.pop(1); runpy.run_path(p, run_name="__main__")'
    Invoke-Native $PythonExe @('-c', $runner, (Join-Path $PSScriptRoot 'smoke_local_xmpp.py'), $connectionPath, '--wait-whatsapp-status')
    $connection = Get-Content -LiteralPath $connectionPath -Raw | ConvertFrom-Json
    $priorPassword = $env:WHATSAPP_CAN_LOCAL_SMOKE_PASSWORD
    try {
        $env:WHATSAPP_CAN_LOCAL_SMOKE_PASSWORD = [string]$connection.password
        Invoke-Native $PythonExe @('-c', $runner, (Join-Path $PSScriptRoot 'smoke_local_upload.py'), '--jid', [string]$connection.jid, '--ca-file', (Join-Path $Directory 'ca.crt'))
    }
    finally { $env:WHATSAPP_CAN_LOCAL_SMOKE_PASSWORD = $priorPassword }
}

function Install-TestPackage {
    param([string]$Name, [string]$Package, [string]$Hash, [string]$Directory)
    Assert-TestName $Name
    & (Join-Path $PSScriptRoot 'install-appliance.ps1') -PackagePath $Package -DistroName $Name `
        -ExpectedPackageSha256 $Hash -InstallOrResume -InstallLocation (Join-Path $Directory 'rootfs') `
        -ConnectionFile (Join-Path $Directory 'connection.json') -CaCertificateFile (Join-Path $Directory 'ca.crt') `
        -MigrationBackupDirectory (Join-Path $Directory 'backups')
}

function Invoke-VoiceSmoke {
    param([string]$Name, [string]$Directory)
    Assert-TestName $Name
    Invoke-Native wsl.exe @('-d', $Name, '-u', 'root', '--', '/usr/local/libexec/whatsapp-can-bridge-voice', 'required')
    Invoke-Native $PythonExe @((Join-Path $PSScriptRoot 'smoke_local_voice.py'), '--ca-file', (Join-Path $Directory 'ca.crt'))
}

function Install-OldFixture {
    param([string]$Name, [string]$Package, [string]$Hash, [string]$Directory)
    try { Install-TestPackage $Name $Package $Hash $Directory }
    catch {
        # The immutable old packages have the former 10 s bootstrap gate.
        # A freshly created fixture must finish its initial schema before it
        # represents an existing healthy installation. Never restart a failing
        # process or retry an arbitrary old-install failure.
        $originalError = $_
        $running = (& wsl.exe -d $Name -u root -- podman inspect whatsapp-can-slidge --format '{{.State.Running}}' | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $running -ne 'true') { throw $originalError }
        $restarts = (& wsl.exe -d $Name -u root -- systemctl show whatsapp-can-slidge.service --property=NRestarts --value | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $restarts -ne '0') { throw $originalError }
        $deadline = [DateTime]::UtcNow.AddSeconds(60)
        $ready = $false
        while ([DateTime]::UtcNow -lt $deadline) {
            & wsl.exe -d $Name -u root -- /usr/local/sbin/whatsapp-can-bridge smoke *> $null
            if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        }
        if (-not $ready) { throw $originalError }
        Install-TestPackage $Name $Package $Hash $Directory
    }
}

foreach ($entry in @(@($modern, $ModernPackageSha256), @($legacy, $LegacyPackageSha256))) {
    if ($entry[1] -notmatch '^[0-9a-f]{64}$' -or
        (Get-FileHash -LiteralPath $entry[0] -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry[1]) {
        throw 'Un fixture público no coincide con su checksum aprobado.'
    }
}

$priorPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $projectRoot
try {
    foreach ($scenario in @('clean', 'legacy', 'modern', 'rollback') | Where-Object { $_ -in $Scenarios }) {
        $name = "WhatsAppCAN-Validate-$testId-$scenario"
        $known = @(& wsl.exe --list --quiet | ForEach-Object { ($_ -replace "`0", '').Trim() })
        if ($LASTEXITCODE -ne 0 -or $known -contains $name) { throw 'La distribución temporal ya existe o no pudo consultarse WSL.' }
        $created.Add($name)
        $directory = Join-Path $outputRoot "$testId-$scenario"
        New-Item -ItemType Directory -Path $directory | Out-Null
        Write-Host "Validación nativa: $scenario ($name)"
        $passed = $false
        try {
            if ($scenario -eq 'clean') {
                Install-TestPackage $name $candidate $candidateHash $directory
                Invoke-LocalSmokes $name $directory
                # Installation must enable voice itself, before any repair command.
                Invoke-VoiceSmoke $name $directory
                Invoke-Native wsl.exe @('-d', $name, '-u', 'root', '--', '/usr/local/sbin/whatsapp-can-bridge', 'smoke')
                Invoke-Native wsl.exe @('--terminate', $name)
                Invoke-Native wsl.exe @('-d', $name, '-u', 'root', '--', '/usr/local/sbin/whatsapp-can-bridge', 'start')
                Invoke-LocalSmokes $name $directory
                Invoke-VoiceSmoke $name $directory
                & wsl.exe -d $name -u root -- /usr/local/sbin/whatsapp-can-bridge voice disable
                if ($LASTEXITCODE -eq 0) { throw 'Se permitió desactivar la función obligatoria.' }
                Invoke-VoiceSmoke $name $directory
            }
            else {
                if ($scenario -eq 'legacy') { Install-OldFixture $name $legacy $LegacyPackageSha256 $directory }
                else { Install-OldFixture $name $modern $ModernPackageSha256 $directory }
                Invoke-Native wsl.exe @('-d', $name, '-u', 'root', '--', '/bin/bash', '-c',
                    'set -eu; printf "synthetic-session\n" > /var/lib/whatsapp-can-bridge/slidge/migration-fixture; printf "synthetic-attachment\n" > /var/lib/whatsapp-can-bridge/attachments/migration-fixture; sed -i "/^SLIDGE_CONVERT_STICKERS=/d" /etc/whatsapp-can-bridge/environment; printf "CAN_MIGRATION_FIXTURE=synthetic-option\nSLIDGE_CONVERT_STICKERS=false\nCAN_VOICE_ENABLE=0\n" >> /etc/whatsapp-can-bridge/environment')
                $before = Get-TestSnapshot $name
                $imageBefore = ''
                if ($scenario -eq 'modern') {
                    Invoke-Native wsl.exe @('-d', $name, '-u', 'root', '--', '/usr/local/sbin/whatsapp-can-bridge', 'update')
                    $imageBefore = (& wsl.exe -d $name -u root -- cat /etc/whatsapp-can-bridge/bridge-image.json | Out-String) | ConvertFrom-Json
                    if ($LASTEXITCODE -ne 0) { throw 'No se pudo comprobar la imagen previa.' }
                }
                if ($scenario -eq 'rollback') {
                    # Force the real post-boot version gate to fail, without
                    # altering the candidate or touching a real WhatsApp account.
                    $tokens = $null; $parseErrors = $null
                    $ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'install-appliance.ps1'), [ref]$tokens, [ref]$parseErrors)
                    foreach ($node in $ast.EndBlock.Statements) {
                        if ($node -is [Management.Automation.Language.FunctionDefinitionAst]) { . ([ScriptBlock]::Create($node.Extent.Text)) }
                    }
                    $failed = $false
                    try {
                        Invoke-LegacyMigration -Distribution $name -NewPackage $candidate -BaseInstallLocation (Join-Path $directory 'rootfs') `
                            -BackupDirectory (Join-Path $directory 'backups') -JournalPath (Join-Path $directory 'backups/legacy-upgrade-journal.json') -TargetVersion ([Version]'99.0.0')
                    }
                    catch { $failed = $true }
                    if (-not $failed) { throw 'La candidata inválida no activó el rollback.' }
                    $restored = (& wsl.exe -d $name -u root -- cat /opt/whatsapp-can-bridge/version.json | Out-String) | ConvertFrom-Json
                    if ($LASTEXITCODE -ne 0 -or [string]$restored.appliance_version -notlike '1.1.0*') { throw 'El rollback no recuperó 1.1.' }
                    $retained = @(Get-ChildItem -LiteralPath (Join-Path $directory 'backups') -Filter '*candidate-*.tar.gz')
                    if ($retained.Count -ne 1) { throw 'No se conservó la candidata fallida.' }
                    Assert-BackupIntegrity -Path $retained[0].FullName
                    if ((Get-PackageApplianceVersion -Path $retained[0].FullName) -ne [Version]'1.2.0') {
                        throw 'El respaldo retenido no corresponde al estado de la candidata nueva.'
                    }
                }
                else { Install-TestPackage $name $candidate $candidateHash $directory }
                if ((Get-TestSnapshot $name) -cne $before) { throw 'Se alteraron credenciales, CA o datos sintéticos al migrar.' }
                if ($scenario -in @('legacy', 'modern') -and $candidateHasVoice) {
                    Invoke-VoiceSmoke $name $directory
                }
                if ($scenario -eq 'modern') {
                    $after = (& wsl.exe -d $name -u root -- cat /etc/whatsapp-can-bridge/bridge-image.json | Out-String) | ConvertFrom-Json
                    if ($LASTEXITCODE -ne 0) { throw 'No se pudo comprobar la imagen migrada.' }
                    $beforeVersion = [int](([string]$imageBefore.image -split ':v')[-1])
                    $bundleVersion = [int](([string]$candidateMetadata.bridge_image -split ':v')[-1])
                    if ($beforeVersion -ge $bundleVersion) {
                        if ($after.image -cne $imageBefore.image -or $after.digest -cne $imageBefore.digest) { throw 'Se degradó una imagen igual o más nueva.' }
                        $previousImage = $after
                    }
                    else {
                        if ($after.image -cne $candidateMetadata.bridge_image -or $after.digest -cne $candidateMetadata.bridge_digest) { throw 'No se adoptó el motor incluido más nuevo.' }
                        $previousImage = (& wsl.exe -d $name -u root -- cat /etc/whatsapp-can-bridge/bridge-image-before-bundled.json | Out-String) | ConvertFrom-Json
                        if ($LASTEXITCODE -ne 0 -or $previousImage.image -cne $imageBefore.image) { throw 'Falta el registro de la imagen anterior.' }
                    }
                    # Preserve a higher installed counter backed by the real
                    # compatible image; counters cannot replace capability checks.
                    Invoke-Native wsl.exe @('-d', $name, '-u', 'root', '--', 'systemctl', 'stop', 'whatsapp-can-slidge.service')
                    $newerImage = $after.PSObject.Copy()
                    $newerImage.image = 'ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v999'
                    $newerFixturePath = Join-Path $directory 'newer-image-fixture.json'
                    [IO.File]::WriteAllText($newerFixturePath, (($newerImage | ConvertTo-Json) + "`n"), [Text.UTF8Encoding]::new($false))
                    $newerFixtureWsl = Convert-ToWslPath -Distribution $name -WindowsPath $newerFixturePath
                    Invoke-Native wsl.exe @('-d', $name, '-u', 'root', '--', 'cp', '--', $newerFixtureWsl, '/etc/whatsapp-can-bridge/bridge-image.json')
                    Invoke-Native wsl.exe @('-d', $name, '-u', 'root', '--', 'chmod', '0600', '/etc/whatsapp-can-bridge/bridge-image.json')
                    Invoke-Native wsl.exe @('-d', $name, '-u', 'root', '--', '/usr/local/libexec/whatsapp-can-bridge-image', 'prefer-bundled')
                    $kept = (& wsl.exe -d $name -u root -- cat /etc/whatsapp-can-bridge/bridge-image.json | Out-String) | ConvertFrom-Json
                    if ($LASTEXITCODE -ne 0 -or $kept.image -cne $newerImage.image -or $kept.image_id -cne $newerImage.image_id) { throw 'Se sustituyó una imagen más nueva por el bundle.' }
                    # An incompatible newer image must be rejected without
                    # silently delivering a configured appliance without voice.
                    $incompatiblePath = Join-Path $directory 'incompatible-image-fixture.json'
                    $previousImage.image = 'ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v1000'
                    [IO.File]::WriteAllText($incompatiblePath, (($previousImage | ConvertTo-Json) + "`n"), [Text.UTF8Encoding]::new($false))
                    $incompatibleWsl = Convert-ToWslPath -Distribution $name -WindowsPath $incompatiblePath
                    Invoke-Native wsl.exe @('-d', $name, '-u', 'root', '--', 'cp', '--', $incompatibleWsl, '/etc/whatsapp-can-bridge/bridge-image.json')
                    & wsl.exe -d $name -u root -- /usr/local/sbin/whatsapp-can-bridge configure
                    if ($LASTEXITCODE -eq 0) { throw 'Se aceptó configurar una imagen sin motor de voz.' }
                    Invoke-Native wsl.exe @('-d', $name, '-u', 'root', '--', 'cp', '--', $newerFixtureWsl, '/etc/whatsapp-can-bridge/bridge-image.json')
                    Invoke-Native wsl.exe @('-d', $name, '-u', 'root', '--', 'chmod', '0600', '/etc/whatsapp-can-bridge/bridge-image.json')
                    Invoke-Native wsl.exe @('-d', $name, '-u', 'root', '--', '/usr/local/sbin/whatsapp-can-bridge', 'start')
                    Invoke-Native wsl.exe @('-d', $name, '-u', 'root', '--', '/usr/local/sbin/whatsapp-can-bridge', 'smoke')
                    Invoke-VoiceSmoke $name $directory
                }
                Invoke-LocalSmokes $name $directory
            }
            $passed = $true
            $results.Add(@{scenario=$scenario; passed=$true})
            Write-Host "PASS native $scenario"
        }
        finally {
            Assert-TestName $name
            & wsl.exe --terminate $name | Out-Null
            if ($passed -and -not $KeepDistributions) { Invoke-Native wsl.exe @('--unregister', $name) }
        }
    }
}
finally {
    $env:PYTHONPATH = $priorPythonPath
    $report = [ordered]@{
        candidate_sha256 = $candidateHash
        expected_scenarios = @('clean', 'legacy', 'modern', 'rollback')
        selected_scenarios = @($Scenarios)
        completed = $results.Count -eq 4
        results = @($results.ToArray())
        distributions = @($created.ToArray())
    }
    [IO.File]::WriteAllText((Join-Path $outputRoot "native-$testId.json"), (($report | ConvertTo-Json -Depth 5) + "`n"), [Text.UTF8Encoding]::new($false))
}
