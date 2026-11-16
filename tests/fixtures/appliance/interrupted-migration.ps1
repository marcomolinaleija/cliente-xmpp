$MigrationBackupDirectory = Join-Path $env:CAN_FIXTURE 'backups'
New-Item -ItemType Directory -Path $MigrationBackupDirectory | Out-Null
$backup = Join-Path $MigrationBackupDirectory 'original.tar.gz'
[IO.File]::WriteAllText($backup, 'original synthetic rootfs')
function Protect-LocalFile { param($Path) }
Write-ProtectedChecksum -Path $backup
$global:events = [Collections.Generic.List[string]]::new()
$global:exists = $true
function Get-WslDistributionNames { if ($global:exists) { return @('Fixture') }; return @() }
function wsl.exe {
    $global:LASTEXITCODE = 0
    $a = @($args); $global:events.Add(($a -join '|'))
    if ($a[0] -eq '--export') { [IO.File]::WriteAllText($a[2], 'candidate synthetic rootfs') }
    if ($a[0] -eq '--unregister') { $global:exists = $false }
    if ($a[0] -eq '--import') { $global:exists = $true }
}
$journal = Join-Path $MigrationBackupDirectory 'journal.json'
Write-MigrationJournal -JournalPath $journal -Journal @{
    schema_version=1; distro_name='Fixture'; phase='restoring_state'; full_backup=$backup
}
Recover-InterruptedMigration -JournalPath $journal -Distribution Fixture -FallbackInstallLocation (Join-Path $env:CAN_FIXTURE 'root')
Assert (-not (Test-Path -LiteralPath $journal)) 'recovery journal not cleared'
$joined = $global:events -join "`n"
Assert ($joined.IndexOf('--export|') -lt $joined.IndexOf('--unregister|')) 'candidate removed before its export'
Assert ($joined.Contains('--import|Fixture|')) 'original distribution not restored'
Assert (@(Get-ChildItem -LiteralPath $MigrationBackupDirectory -Filter '*candidate*.sha256').Count -eq 1) 'candidate backup not protected'
$global:events.Clear()
Write-MigrationJournal -JournalPath $journal -Journal @{
    schema_version=1; distro_name='Fixture'; phase='backup_ready'; full_backup=$backup
}
Recover-InterruptedMigration -JournalPath $journal -Distribution Fixture -FallbackInstallLocation (Join-Path $env:CAN_FIXTURE 'root')
Assert (-not (($global:events -join "`n").Contains('--unregister|'))) 'pre-replacement interruption replaced existing rootfs'
Assert (($global:events -join "`n").Contains('|start')) 'original services not resumed'
$global:events.Clear()
Write-MigrationJournal -JournalPath $journal -Journal @{
    schema_version=1; distro_name='Fixture'; phase='replacing'; full_backup=(Join-Path $env:CAN_FIXTURE 'outside.tar.gz')
}
$rejected = $false
try { Recover-InterruptedMigration -JournalPath $journal -Distribution Fixture -FallbackInstallLocation (Join-Path $env:CAN_FIXTURE 'root') } catch { $rejected = $true }
Assert $rejected 'out-of-root recovery path accepted'
Assert ($global:events.Count -eq 0) 'unsafe journal reached WSL'
