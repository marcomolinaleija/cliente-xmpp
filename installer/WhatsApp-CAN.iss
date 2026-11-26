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
  BridgeInstallLogPath: String;

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
    '" -ExpectedPackageSha256 "{#WslPackageSha256}" -InstallOrResume' +
    ' -LogPath "' + BridgeInstallLogPath + '"';
end;

// Keep in step with Get-InstallFailure in install-appliance.ps1 (same exit codes).
function GetBridgeFailureHint(Code: Integer): String;
begin
  case Code of
    10: Result := 'Otro programa ya usa un puerto que necesita el puente (5222, 5280, 8080 u 8766). ' +
      'Ciérralo (otro servidor XMPP, Docker o un servidor web en el puerto 8080) y vuelve a intentarlo.';
    11: Result := 'Quedaron archivos de un intento anterior en la carpeta del puente local. ' +
      'Si "wsl -l -v" no muestra WhatsAppCAN-Bridge, cambia el nombre de la carpeta indicada en el registro y vuelve a intentarlo.';
    12: Result := 'La virtualización no está disponible para WSL2. Actívala en la BIOS/UEFI junto con ' +
      'la característica "Plataforma de máquina virtual", ejecuta "wsl --install --no-distribution" y reinicia Windows.';
    13: Result := 'WSL no está instalado o está desactualizado. Ejecuta "wsl --update", reinicia Windows y vuelve a intentarlo.';
    14: Result := 'No hay espacio suficiente en el disco. Libera al menos 4 GB y vuelve a intentarlo.';
    15: Result := 'El paquete descargado del puente está incompleto. Vuelve a intentarlo para descargarlo de nuevo.';
    16: Result := 'Ya hay un puente local instalado que no se puede reemplazar automáticamente. ' +
      'Usa el actualizador del puente local o comparte el registro; no se borró nada.';
    17: Result := 'Hay otra instalación o una migración sin terminar. Espera o reinicia Windows y vuelve a intentarlo.';
    18: Result := 'El puente se instaló, pero sus servicios no terminaron de configurarse. Vuelve a intentarlo; ' +
      'se retoma sin borrar datos.';
  else
    Result := 'No se reconoció la causa. Comparte el registro para revisarlo.';
  end;
end;

function CopyBridgeLogToClipboard: Boolean;
var
  ResultCode: Integer;
  QuotedPath: String;
begin
  QuotedPath := BridgeInstallLogPath;
  StringChangeEx(QuotedPath, '''', '''''', True);
  Result :=
    Exec(
      ExpandConstant('{sysnative}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoProfile -NonInteractive -Command "Get-Content -Raw -Encoding UTF8 -LiteralPath ''' +
        QuotedPath + ''' | Set-Clipboard"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode
    ) and (ResultCode = 0);
end;

procedure OfferBridgeLog(Hint: String);
var
  Choice: Integer;
  Status: String;
  ErrorCode: Integer;
begin
  if WizardSilent or not FileExists(BridgeInstallLogPath) then
    Exit;
  Status := '';
  repeat
    Choice := TaskDialogMsgBox(
      'No se pudo configurar el puente local.',
      Hint + #13#13 + 'Registro: ' + BridgeInstallLogPath + Status,
      mbError, MB_YESNOCANCEL, ['Copiar registro', 'Abrir registro'], 0);
    if Choice = IDYES then
    begin
      if CopyBridgeLogToClipboard then
        Status := #13#13 + 'El registro se copió al portapapeles.'
      else
        Status := #13#13 + 'No se pudo copiar el registro; usa "Abrir registro".';
    end
    else if Choice = IDNO then
    begin
      if not ShellExec('open', 'notepad.exe', '"' + BridgeInstallLogPath + '"', '',
          SW_SHOWNORMAL, ewNoWait, ErrorCode) then
        Status := #13#13 + 'No se pudo abrir el registro.';
    end;
  until Choice = IDCANCEL;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Hint: String;
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
  // Each attempt gets its own log; the hidden PowerShell window shows nothing.
  BridgeInstallLogPath :=
    ExpandConstant('{localappdata}\WhatsAppCAN\logs\bridge-install-') +
    GetDateTimeString('yyyymmddhhnnss', #0, #0) + '.log';
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
    Hint := GetBridgeFailureHint(ResultCode);
    OfferBridgeLog(Hint);
    Result :=
      'No se pudo completar la configuración del puente local.'#13#13 + Hint + #13#13 +
      'El instalador puede volver a intentarlo sin borrar los datos ya creados. ' +
      'Código de salida: ' + IntToStr(ResultCode) + '.'#13#13 +
      'Detalles en: ' + BridgeInstallLogPath;
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
