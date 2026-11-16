
$global:installed = '1.1.0'; $global:voicePresent = $false; $global:voiceEnabled = $false
function wsl.exe {
    $global:LASTEXITCODE = 0
    if ($args -contains 'cat') { return ('{"appliance_version":"' + $global:installed + '"}') }
    if ($args -contains '/usr/local/libexec/whatsapp-can-bridge-voice' -and -not $global:voicePresent) { $global:LASTEXITCODE = 1 }
    if ($args -contains 'required' -and -not $global:voiceEnabled) { $global:LASTEXITCODE = 1 }
}
Assert ((Get-InstalledApplianceVersion -Distribution Fixture) -eq [Version]'1.1.0') 'wrong installed version'
$global:installed = '1.0.0'
Assert (Test-TargetAppliance -Distribution Fixture -TargetVersion ([Version]'1.0.0')) 'healthy legacy fixture cannot resume'
$global:installed = '1.1.0'
Assert (-not (Test-TargetAppliance -Distribution Fixture -TargetVersion ([Version]'1.2.0'))) '1.1 accepted as 1.2'
$global:installed = '1.2.0'
Assert (-not (Test-TargetAppliance -Distribution Fixture -TargetVersion ([Version]'1.2.0'))) 'missing voice helper accepted'
$global:voicePresent = $true
Assert (-not (Test-TargetAppliance -Distribution Fixture -TargetVersion ([Version]'1.2.0'))) 'disabled voice accepted'
$global:voiceEnabled = $true
Assert (Test-TargetAppliance -Distribution Fixture -TargetVersion ([Version]'1.2.0')) 'valid target rejected'
$global:installed = 'untrusted'
$rejected = $false
try { Get-InstalledApplianceVersion -Distribution Fixture } catch { $rejected = $true }
Assert $rejected 'unknown version accepted'
