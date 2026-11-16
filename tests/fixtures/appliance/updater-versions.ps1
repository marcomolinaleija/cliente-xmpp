
$global:installed = '1.0.0'; $global:voicePresent = $false; $global:voiceEnabled = $false
function wsl.exe {
    $global:LASTEXITCODE = 0
    if ($args -contains 'cat') { return ('{"appliance_version":"' + $global:installed + '"}') }
    if ($args -contains '/usr/local/libexec/whatsapp-can-bridge-voice' -and -not $global:voicePresent) { $global:LASTEXITCODE = 1 }
    if ($args -contains 'required' -and -not $global:voiceEnabled) { $global:LASTEXITCODE = 1 }
}
foreach ($old in @('1.0.0', '1.1.0', '1.1.0-dev')) {
    $global:installed = $old
    Assert (-not (Test-ModernAppliance -Distribution Fixture -TargetVersion ([Version]'1.2.0'))) 'old version skipped'
}
$global:installed = '1.2.0'
Assert (-not (Test-ModernAppliance -Distribution Fixture -TargetVersion ([Version]'1.2.0'))) 'missing capability skipped'
$global:voicePresent = $true
Assert (-not (Test-ModernAppliance -Distribution Fixture -TargetVersion ([Version]'1.2.0'))) 'disabled voice skipped'
$global:voiceEnabled = $true
Assert (Test-ModernAppliance -Distribution Fixture -TargetVersion ([Version]'1.2.0')) 'current version not skipped'
$global:installed = '1.3.0'
$rejected = $false
try { Test-ModernAppliance -Distribution Fixture -TargetVersion ([Version]'1.2.0') } catch { $rejected = $true }
Assert $rejected 'downgrade accepted'
foreach ($url in @('http://github.com/x', 'https://example.invalid/x', 'file:///tmp/x')) {
    $rejected = $false
    try { Assert-DownloadUri -Value $url -AllowedHosts @('github.com') } catch { $rejected = $true }
    Assert $rejected 'foreign URL accepted'
}
