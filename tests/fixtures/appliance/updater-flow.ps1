# Exercise the whole public updater with local verified assets and a fake installer.
$global:installed = '1.1.0'
$global:downloads = 0
$global:manifestText = ''
function wsl.exe {
    $global:LASTEXITCODE = 0
    if ($args -contains '--list') { return 'Fixture' }
    if ($args -contains 'cat') { return ('{"appliance_version":"' + $global:installed + '"}') }
}
$download = Join-Path $env:CAN_FIXTURE 'download'
New-Item -ItemType Directory -Path $download | Out-Null
$package = Join-Path $download 'WhatsAppCAN-Bridge-amd64.wsl'
$installer = Join-Path $download 'install-appliance.ps1'
[IO.File]::WriteAllText($package, 'synthetic package')
$installerCode = @'
param($PackagePath, $DistroName, $ExpectedPackageSha256, $ExpectedApplianceVersion, [switch]$InstallOrResume)
if ($DistroName -ne 'Fixture' -or -not $InstallOrResume -or $ExpectedApplianceVersion -ne '1.2.0') { throw 'incorrect arguments' }
if ((Get-FileHash -LiteralPath $PackagePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedPackageSha256) { throw 'package hash not forwarded' }
[IO.File]::WriteAllText((Join-Path (Split-Path $PackagePath -Parent) 'installer-ran'), 'verified')
'@
[IO.File]::WriteAllText($installer, $installerCode, [Text.UTF8Encoding]::new($true))
$manifest = @{
    schema_version=1; appliance_version='1.2.0';
    download_url='https://github.com/example/fixture/releases/download/v1/package.wsl';
    sha256=(Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash.ToLowerInvariant();
    size_bytes=(Get-Item -LiteralPath $package).Length;
    installer_url='https://github.com/example/fixture/releases/download/v1/install.ps1';
    installer_sha256=(Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
}
$global:manifestText = $manifest | ConvertTo-Json
function Invoke-WebRequest {
    param($Uri, [switch]$UseBasicParsing, $OutFile)
    if ($OutFile) { throw 'unexpected network download of preverified assets' }
    $global:downloads++
    return [PSCustomObject]@{Content=$global:manifestText}
}
$nativeModulePath = $env:PSModulePath
try {
    # Simulate a Core-only caller. The installer is a fresh Windows PowerShell
    # process and must still resolve its own built-in hashing cmdlet.
    $env:PSModulePath = $env:CAN_FIXTURE
    & $env:CAN_SOURCE -DistroName Fixture -DownloadDirectory $download -Si -ConservarDescargas
}
finally { $env:PSModulePath = $nativeModulePath }
Assert (Test-Path -LiteralPath (Join-Path $download 'installer-ran')) 'verified installer not executed'
# Up-to-date check must not invoke the installer again.
Remove-Item -LiteralPath (Join-Path $download 'installer-ran')
$global:installed = '1.2.0'
& $env:CAN_SOURCE -DistroName Fixture -DownloadDirectory $download -Si -ConservarDescargas
Assert (-not (Test-Path -LiteralPath (Join-Path $download 'installer-ran'))) 'current installation migrated again'
# Invalid metadata fails before touching an asset or invoking its installer.
$global:installed = '1.1.0'
$manifest.installer_sha256 = 'invalid'
$global:manifestText = $manifest | ConvertTo-Json
$rejected = $false
try { & $env:CAN_SOURCE -DistroName Fixture -DownloadDirectory $download -Si } catch { $rejected = $true }
Assert $rejected 'invalid installer hash accepted'
Assert (-not (Test-Path -LiteralPath (Join-Path $download 'installer-ran'))) 'invalid installer executed'
