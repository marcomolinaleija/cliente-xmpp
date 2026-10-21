
$global:events = [Collections.Generic.List[string]]::new()
$global:installed = '1.1.0'; $global:exists = $true; $global:failConfigure = $false
$global:active = $true
$global:failCandidateExport = $false
function Protect-LocalFile { param($Path) }
function Test-LocalTcpPort { param($Port) return $false }
function Convert-ToWslPath { param($Distribution, $WindowsPath) return $WindowsPath }
function Get-WslDistributionNames { if ($global:exists) { return @('Fixture') }; return @() }
function wsl.exe {
    $a = @($args); $global:LASTEXITCODE = 0
    $global:events.Add(($a -join '|'))
    if ($a[0] -eq '--export') {
        if ($global:failCandidateExport -and $a[2] -like '*candidate*') { $global:LASTEXITCODE = 1; return }
        [IO.File]::WriteAllText($a[2], $global:installed); return
    }
    if ($a[0] -eq '--unregister') { $global:exists = $false; return }
    if ($a[0] -eq '--install') { $global:exists = $true; $global:installed = '1.2.0'; return }
    if ($a[0] -eq '--import') { $global:exists = $true; $global:installed = [IO.File]::ReadAllText($a[3]); return }
    if ($a -contains 'cat') {
        if ($a[-1] -eq '/opt/whatsapp-can-bridge/version.json') { return ('{"appliance_version":"' + $global:installed + '"}') }
        return ('{"schema_version":1,"image":"ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v35","digest":"sha256:' + ('a'*64) + '","image_id":"sha256:' + ('b'*64) + '"}')
    }
    if ($a -contains 'test' -and $a -contains '/etc/whatsapp-can-bridge/bridge-image.json' -and -not $global:active) { $global:LASTEXITCODE = 1; return }
    if ($a -contains '-czf') { [IO.File]::WriteAllText($a[([Array]::IndexOf($a, '-czf') + 1)], 'synthetic state'); return }
    if ($a -contains 'save') { [IO.File]::WriteAllText($a[([Array]::IndexOf($a, '-o') + 1)], 'synthetic image'); return }
    if ($a -contains 'inspect') { return ('c'*64) }
    if ($a[-1] -eq 'configure' -and $global:failConfigure -and $global:installed -eq '1.2.0') { $global:LASTEXITCODE = 1 }
}
$backupRoot = Join-Path $env:CAN_FIXTURE 'success'
$journal = Join-Path $backupRoot 'journal.json'
Invoke-LegacyMigration -Distribution Fixture -NewPackage fixture.wsl -BaseInstallLocation (Join-Path $env:CAN_FIXTURE 'root') -BackupDirectory $backupRoot -JournalPath $journal -TargetVersion ([Version]'1.2.0')
Assert ($global:installed -eq '1.2.0') 'migration failed'
Assert (-not (Test-Path -LiteralPath $journal)) 'journal not removed'
$joined = $global:events -join "`n"
Assert ($joined.IndexOf('|save|') -lt $joined.IndexOf('--unregister|')) 'image not backed up before removal'
Assert ($joined.Contains('|load|-i|')) 'active image not imported'
Assert ($joined.Contains('|adopt-imported|localhost/whatsapp-can-migration:')) 'normalized image ID not persisted'
Assert (@(Get-ChildItem -LiteralPath $backupRoot -Filter '*image.oci.sha256').Count -eq 1) 'image checksum missing'
$global:installed = '1.1.0'; $global:events.Clear(); $global:failConfigure = $true
$backupRoot = Join-Path $env:CAN_FIXTURE 'failure'
$failed = $false
try {
    Invoke-LegacyMigration -Distribution Fixture -NewPackage fixture.wsl -BaseInstallLocation (Join-Path $env:CAN_FIXTURE 'failed-root') -BackupDirectory $backupRoot -JournalPath (Join-Path $backupRoot 'journal.json') -TargetVersion ([Version]'1.2.0')
} catch { $failed = $true }
Assert $failed 'failure incorrectly reported as success'
Assert ($global:installed -eq '1.1.0') 'old rootfs not restored'
$candidates = @(Get-ChildItem -LiteralPath $backupRoot -Filter '*candidate-*.tar.gz')
Assert ($candidates.Count -eq 1) 'failed candidate discarded'
Assert ([IO.File]::ReadAllText($candidates[0].FullName) -eq '1.2.0') 'wrong candidate state retained'
Assert-BackupIntegrity -Path $candidates[0].FullName
$global:installed = '1.0.0'; $global:events.Clear()
$global:failConfigure = $false; $global:active = $false
$backupRoot = Join-Path $env:CAN_FIXTURE 'legacy'
Invoke-LegacyMigration -Distribution Fixture -NewPackage fixture.wsl -BaseInstallLocation (Join-Path $env:CAN_FIXTURE 'legacy-root') -BackupDirectory $backupRoot -JournalPath (Join-Path $backupRoot 'journal.json') -TargetVersion ([Version]'1.2.0')
Assert ($global:installed -eq '1.2.0') 'legacy migration failed'
Assert (-not (($global:events -join "`n").Contains('|save|'))) 'legacy migration tried to save nonexistent image metadata'
$global:events.Clear(); $global:failCandidateExport = $true
$backup = @(Get-ChildItem -LiteralPath $backupRoot -Filter '*full.tar.gz')[0].FullName
$failed = $false
try {
    Restore-LegacyBackup -Distribution Fixture -BackupPath $backup -RestoreLocation (Join-Path $env:CAN_FIXTURE 'unsafe-rollback')
} catch { $failed = $true }
Assert $failed 'failed candidate export was ignored'
Assert (-not (($global:events -join "`n").Contains('--unregister|'))) 'candidate removed after failed preservation'
Assert ($global:installed -eq '1.2.0') 'candidate lost after failed preservation'
