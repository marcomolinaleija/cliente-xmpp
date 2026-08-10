#ifndef MyAppVersion
  #error MyAppVersion must be provided by build_release.ps1
#endif

#ifndef SourceDir
  #error SourceDir must point to the PyInstaller onedir output
#endif

#ifndef OutputDir
  #error OutputDir must point to the release directory
#endif

#ifndef WslPackageUrl
  #error WslPackageUrl must point to the immutable appliance release asset
#endif

#ifndef WslPackageName
  #error WslPackageName must contain the appliance asset name
#endif

#ifndef WslPackageSha256
  #error WslPackageSha256 must contain the appliance SHA-256
#endif

#ifndef WslPackageSizeMb
  #error WslPackageSizeMb must contain the approximate download size in MiB
#endif

#ifndef WslInstallScript
  #error WslInstallScript must point to install-appliance.ps1
#endif

#define MyAppName "WhatsApp CAN"
#define MyAppExeName "WhatsApp-CAN.exe"
#define MyAppPublisher "Marco ML"
#define MyAppURL "https://github.com/marcomolinaleija/cliente-xmpp"

[Setup]
AppId={{90A40C2A-E8A1-41EE-97CF-E77AA6380698}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=WhatsApp-CAN-{#MyAppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Instalador de {#MyAppName}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos adicionales:"; Flags: unchecked

[Files]
Source: "{#WslInstallScript}"; Flags: dontcopy
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--set-connection-mode {code:GetSelectedConnectionMode}"; StatusMsg: "Guardando la forma de conexión elegida..."; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Iniciar {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  UsagePage: TInputOptionWizardPage;
  BridgePackageReady: Boolean;
  BridgeInstallReady: Boolean;

function ShouldInstallLocalBridge: Boolean;
begin
  Result := Assigned(UsagePage) and (UsagePage.SelectedValueIndex = 0);
end;

function WslIsAvailable: Boolean;
var
  ResultCode: Integer;
begin
  Result :=
    Exec(
      ExpandConstant('{sysnative}\wsl.exe'),
      '--version',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    ) and (ResultCode = 0);
end;

procedure InitializeWizard;
var
  RequestedMode: String;
  PreviousMode: String;
begin
  UsagePage := CreateInputOptionPage(
    wpSelectDir,
    'Forma de conexión',
    '¿Cómo quieres usar WhatsApp CAN?',
    'Elige dónde se ejecutará el puente de WhatsApp. Podrás cambiar entre ambos perfiles después.',
    True,
    False
  );
  UsagePage.Add(
    'Puente local en este equipo (recomendado). Mantiene la sesión en tu PC y descarga aproximadamente {#WslPackageSizeMb} MiB.'
  );
  UsagePage.Add(
    'Servidor XMPP o VPS. Instala solamente el cliente y no descarga el puente local.'
  );

  RequestedMode := Lowercase(ExpandConstant('{param:connectionmode|}'));
  PreviousMode := GetPreviousData('ConnectionMode', '');
  if RequestedMode = 'remote' then
    UsagePage.SelectedValueIndex := 1
  else if RequestedMode = 'local' then
    UsagePage.SelectedValueIndex := 0
  else if PreviousMode = 'remote' then
    UsagePage.SelectedValueIndex := 1
  else
    UsagePage.SelectedValueIndex := 0;
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  if ShouldInstallLocalBridge then
    SetPreviousData(PreviousDataKey, 'ConnectionMode', 'local')
  else
    SetPreviousData(PreviousDataKey, 'ConnectionMode', 'remote');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = UsagePage.ID) and ShouldInstallLocalBridge and not WslIsAvailable then
  begin
    MsgBox(
      'El puente local requiere WSL2 actualizado.'#13#13 +
      'Instala WSL desde una terminal de Windows con el comando:'#13 +
      'wsl --install --no-distribution'#13#13 +
      'Después reinicia Windows y vuelve a ejecutar este instalador. También puedes volver y elegir Servidor XMPP o VPS.',
      mbError,
      MB_OK
    );
    Result := False;
  end;
end;

function GetBridgeInstallParameters(Param: String): String;
begin
  Result :=
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\') + ExtractFileName('{#WslInstallScript}') +
    '" -PackagePath "' + ExpandConstant('{tmp}\{#WslPackageName}') +
    '" -ExpectedPackageSha256 "{#WslPackageSha256}" -InstallOrResume';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  if not ShouldInstallLocalBridge or BridgeInstallReady then
    Exit;

  if not WslIsAvailable then
  begin
    Result :=
      'El puente local requiere WSL2 actualizado. Instala WSL con "wsl --install --no-distribution", reinicia Windows y vuelve a ejecutar el instalador.';
    Exit;
  end;

  if not BridgePackageReady then
  begin
    try
      DownloadTemporaryFile(
        '{#WslPackageUrl}',
        '{#WslPackageName}',
        '{#WslPackageSha256}',
        nil
      );
      ExtractTemporaryFile(ExtractFileName('{#WslInstallScript}'));
      BridgePackageReady := True;
    except
      Result :=
        'No se pudo descargar y verificar el puente local.'#13#13 +
        GetExceptionMessage;
      Exit;
    end;
  end;

  WizardForm.StatusLabel.Caption :=
    'Configurando el puente local de WhatsApp. Esto puede tardar varios minutos...';
  if not Exec(
    ExpandConstant('{sysnative}\WindowsPowerShell\v1.0\powershell.exe'),
    GetBridgeInstallParameters(''),
    ExpandConstant('{tmp}'),
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
  begin
    Result :=
      'No se pudo iniciar la configuración del puente local: ' +
      SysErrorMessage(ResultCode) + '.';
    Exit;
  end;
  if ResultCode <> 0 then
  begin
    Result :=
      'No se pudo completar la configuración del puente local. ' +
      'El instalador puede volver a intentarlo sin borrar los datos ya creados. ' +
      'Código de salida: ' + IntToStr(ResultCode) + '.';
    Exit;
  end;
  BridgeInstallReady := True;
end;

function GetSelectedConnectionMode(Param: String): String;
begin
  if ShouldInstallLocalBridge then
    Result := 'local'
  else
    Result := 'remote';
end;
