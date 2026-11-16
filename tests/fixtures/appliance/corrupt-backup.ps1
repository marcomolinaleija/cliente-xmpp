
$backup = Join-Path $env:CAN_FIXTURE 'backup.tar.gz'
[IO.File]::WriteAllText($backup, 'changed')
[IO.File]::WriteAllText("$backup.sha256", (('a'*64) + ' backup.tar.gz'))
function Get-WslDistributionNames { throw 'WSL must not be accessed after corrupt backup' }
$rejected = $false
try { Restore-LegacyBackup -Distribution Fixture -BackupPath $backup -RestoreLocation (Join-Path $env:CAN_FIXTURE 'restore') } catch { $rejected = $_.Exception.Message -like '*SHA-256*' }
Assert $rejected 'corrupt backup did not fail before WSL access'
