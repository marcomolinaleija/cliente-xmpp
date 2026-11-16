$name = 'Fixture-' + [Guid]::NewGuid().ToString('N')
$lock = Enter-ApplianceMigrationLock -Distribution $name
try {
    $childCode = @'
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($env:CAN_SOURCE, [ref]$tokens, [ref]$errors)
foreach ($node in $ast.EndBlock.Statements) {
    if ($node -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([ScriptBlock]::Create($node.Extent.Text))
    }
}
try {
    $unexpected = Enter-ApplianceMigrationLock -Distribution $env:CAN_LOCK_FIXTURE
    $unexpected.ReleaseMutex(); $unexpected.Dispose()
    exit 2
} catch {
    if ($_.Exception.Message -like '*migración activa*') { exit 0 }
    throw
}
'@
    $env:CAN_LOCK_FIXTURE = $name.ToUpperInvariant()
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childCode))
    & powershell.exe -NoProfile -NonInteractive -EncodedCommand $encoded
    Assert ($LASTEXITCODE -eq 0) 'another process entered the same migration'
}
finally { $lock.ReleaseMutex(); $lock.Dispose() }
$replacement = Enter-ApplianceMigrationLock -Distribution $name
$replacement.ReleaseMutex(); $replacement.Dispose()
