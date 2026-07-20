@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul 2>&1

REM =====================================================================
REM  WhatsApp CAN - Publicador de releases estables
REM  No almacena credenciales: usa la sesión existente de GitHub CLI.
REM =====================================================================

set "REPO=marcomolinaleija/cliente-xmpp"
set "PROJECT_ROOT=%~dp0"
set "RELEASE_DIR=%PROJECT_ROOT%release"
set "NOTES_FILE=%PROJECT_ROOT%release_notes.txt"
set "VERSION=%~1"
set "MODE=%~2"
set "CONFIRM_ARG=%~3"
set "AUTO_CONFIRM=n"
set "TAG="
set "SOURCE_VERSION="
set "PROJECT_VERSION="
set "CURRENT_BRANCH="
set "HEAD_COMMIT="
set "REMOTE_COMMIT="
set "RELEASE_URL="

if not "%~4"=="" goto usage
if not "%CONFIRM_ARG%"=="" if /I not "%CONFIRM_ARG%"=="--yes" goto usage
if /I "%CONFIRM_ARG%"=="--yes" set "AUTO_CONFIRM=s"

cd /d "%PROJECT_ROOT%"
if errorlevel 1 (
    echo ERROR: No se pudo abrir la carpeta del proyecto.
    exit /b 1
)

echo.
echo =====================================================================
echo   WhatsApp CAN - Publicador de release estable
echo =====================================================================
echo.

REM =====================================================================
REM  1. Leer y validar la versión del proyecto
REM =====================================================================
echo [1/7] Verificando la versión...

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no esta disponible en PATH.
    exit /b 1
)

for /f "usebackq delims=" %%V in (`python -c "from cliente_xmpp import __version__; print(__version__)" 2^>nul`) do set "SOURCE_VERSION=%%V"
for /f "usebackq delims=" %%V in (`python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])" 2^>nul`) do set "PROJECT_VERSION=%%V"

if not defined SOURCE_VERSION (
    echo ERROR: No se pudo leer cliente_xmpp.__version__.
    exit /b 1
)
if not defined PROJECT_VERSION (
    echo ERROR: No se pudo leer la versión de pyproject.toml.
    exit /b 1
)
if not "%SOURCE_VERSION%"=="%PROJECT_VERSION%" (
    echo ERROR: Las versiones no coinciden.
    echo        cliente_xmpp=%SOURCE_VERSION%
    echo        pyproject=%PROJECT_VERSION%
    exit /b 1
)

if not defined VERSION set "VERSION=%SOURCE_VERSION%"
if not "%VERSION%"=="%SOURCE_VERSION%" (
    echo ERROR: La versión solicitada %VERSION% no coincide con el proyecto %SOURCE_VERSION%.
    exit /b 1
)

set "V_MAJOR="
set "V_MINOR="
set "V_PATCH="
set "V_EXTRA="
for /f "tokens=1-4 delims=." %%A in ("%VERSION%") do (
    set "V_MAJOR=%%A"
    set "V_MINOR=%%B"
    set "V_PATCH=%%C"
    set "V_EXTRA=%%D"
)
if not defined V_MAJOR goto invalid_version
if not defined V_MINOR goto invalid_version
if not defined V_PATCH goto invalid_version
if defined V_EXTRA goto invalid_version
for /f "delims=0123456789" %%A in ("%V_MAJOR%%V_MINOR%%V_PATCH%") do goto invalid_version

findstr /L /C:"filevers=(%V_MAJOR%, %V_MINOR%, %V_PATCH%, 0)" "windows_version_info.txt" >nul
if errorlevel 1 (
    echo ERROR: windows_version_info.txt no contiene filevers para %VERSION%.
    exit /b 1
)
findstr /L /C:"prodvers=(%V_MAJOR%, %V_MINOR%, %V_PATCH%, 0)" "windows_version_info.txt" >nul
if errorlevel 1 (
    echo ERROR: windows_version_info.txt no contiene prodvers para %VERSION%.
    exit /b 1
)
findstr /L /C:"StringStruct(u'FileVersion', u'%VERSION%')" "windows_version_info.txt" >nul
if errorlevel 1 (
    echo ERROR: windows_version_info.txt no contiene FileVersion=%VERSION%.
    exit /b 1
)
findstr /L /C:"StringStruct(u'ProductVersion', u'%VERSION%')" "windows_version_info.txt" >nul
if errorlevel 1 (
    echo ERROR: windows_version_info.txt no contiene ProductVersion=%VERSION%.
    exit /b 1
)

set "TAG=v%VERSION%"
set "ZIP_NAME=WhatsApp-CAN-%VERSION%.zip"
set "ZIP_PATH=%RELEASE_DIR%\%ZIP_NAME%"
set "ZIP_CHECKSUM_NAME=%ZIP_NAME%.sha256"
set "ZIP_CHECKSUM_PATH=%RELEASE_DIR%\%ZIP_CHECKSUM_NAME%"
set "INSTALLER_NAME=WhatsApp-CAN-%VERSION%-Setup.exe"
set "INSTALLER_PATH=%RELEASE_DIR%\%INSTALLER_NAME%"
set "INSTALLER_CHECKSUM_NAME=%INSTALLER_NAME%.sha256"
set "INSTALLER_CHECKSUM_PATH=%RELEASE_DIR%\%INSTALLER_CHECKSUM_NAME%"

echo       Versión: %VERSION%
echo       Tag:     %TAG%

REM =====================================================================
REM  2. Comprobar los artefactos obligatorios
REM =====================================================================
echo [2/7] Verificando los archivos compilados...

if not exist "%RELEASE_DIR%\" goto need_build
if not exist "%ZIP_PATH%" goto need_build
if not exist "%ZIP_CHECKSUM_PATH%" goto need_build
for %%A in ("%ZIP_PATH%") do if %%~zA LEQ 0 goto need_build
for %%A in ("%ZIP_CHECKSUM_PATH%") do if %%~zA LEQ 0 goto need_build

if not exist "%NOTES_FILE%" (
    echo ERROR: No existe release_notes.txt en la raíz del proyecto.
    exit /b 1
)
for %%A in ("%NOTES_FILE%") do if %%~zA LEQ 0 (
    echo ERROR: release_notes.txt esta vacío.
    exit /b 1
)

python tools\validate_release.py "%ZIP_PATH%" "%ZIP_CHECKSUM_PATH%"
if errorlevel 1 (
    echo ERROR: El ZIP o su firma SHA-256 no superaron la validación.
    echo        Vuelve a ejecutar build_release.ps1 antes de publicar.
    exit /b 1
)

REM =====================================================================
REM  3. Elegir qué archivos publicar
REM =====================================================================
echo [3/7] Seleccionando los archivos de la release...

if not defined MODE goto prompt_mode
goto normalize_mode

:prompt_mode
echo.
echo   1. ZIP del programa y firma SHA-256
echo   2. ZIP, firma, instalador EXE y firma del instalador
set /p "MODE=Elige 1 o 2: "

:normalize_mode
if /I "%MODE%"=="1" set "MODE=zip"
if /I "%MODE%"=="base" set "MODE=zip"
if /I "%MODE%"=="core" set "MODE=zip"
if /I "%MODE%"=="2" set "MODE=installer"
if /I "%MODE%"=="full" set "MODE=installer"
if /I "%MODE%"=="completo" set "MODE=installer"

if /I "%MODE%"=="zip" goto mode_ok
if /I "%MODE%"=="installer" goto installer_mode
echo ERROR: Modo inválido. Usa zip o installer.
exit /b 2

:installer_mode
if not exist "%INSTALLER_PATH%" goto need_build
if not exist "%INSTALLER_CHECKSUM_PATH%" goto need_build
for %%A in ("%INSTALLER_PATH%") do if %%~zA LEQ 0 goto need_build
for %%A in ("%INSTALLER_CHECKSUM_PATH%") do if %%~zA LEQ 0 goto need_build

set "WC_INSTALLER_PATH=%INSTALLER_PATH%"
set "WC_INSTALLER_CHECKSUM_PATH=%INSTALLER_CHECKSUM_PATH%"
powershell -NoProfile -NonInteractive -Command "$text=[IO.File]::ReadAllText($env:WC_INSTALLER_CHECKSUM_PATH); if($text -notmatch '(?i)\b[0-9a-f]{64}\b'){exit 1}; $actual=(Get-FileHash -LiteralPath $env:WC_INSTALLER_PATH -Algorithm SHA256).Hash; if($actual -ine $matches[0]){exit 1}"
set "INSTALLER_HASH_EXIT=%ERRORLEVEL%"
set "WC_INSTALLER_PATH="
set "WC_INSTALLER_CHECKSUM_PATH="
if not "%INSTALLER_HASH_EXIT%"=="0" (
    echo ERROR: El instalador o su firma SHA-256 no superaron la validación.
    echo        Vuelve a ejecutar build_release.ps1 antes de publicar.
    exit /b 1
)

:mode_ok
if /I "%MODE%"=="installer" (
    set "MODE_DESCRIPTION=ZIP, firmas e instalador"
) else (
    set "MODE_DESCRIPTION=ZIP y firma SHA-256"
)
echo       Modo: %MODE_DESCRIPTION%

REM =====================================================================
REM  4. Validar Git y GitHub CLI sin mostrar credenciales
REM =====================================================================
echo [4/7] Verificando Git y GitHub CLI...

where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: Git no esta disponible en PATH.
    exit /b 1
)
where gh >nul 2>&1
if errorlevel 1 (
    echo ERROR: GitHub CLI no esta disponible en PATH.
    echo        Instálalo desde https://cli.github.com/
    exit /b 1
)
gh auth status >nul 2>&1
if errorlevel 1 (
    echo ERROR: GitHub CLI no tiene una sesión válida. Ejecuta: gh auth login
    exit /b 1
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: El script no se esta ejecutando dentro de un repositorio Git.
    exit /b 1
)
for /f "delims=" %%R in ('git remote get-url origin 2^>nul') do set "REMOTE_URL=%%R"
if not defined REMOTE_URL (
    echo ERROR: No existe el remoto origin.
    exit /b 1
)
git remote get-url origin 2>nul | findstr /I /L /C:"marcomolinaleija/cliente-xmpp" >nul
if errorlevel 1 (
    echo ERROR: El remoto origin no corresponde a %REPO%.
    exit /b 1
)

git diff --quiet --ignore-submodules --
if errorlevel 1 goto dirty_tree
git diff --cached --quiet --ignore-submodules --
if errorlevel 1 goto dirty_tree
for /f "delims=" %%S in ('git ls-files --others --exclude-standard') do goto dirty_tree

for /f "delims=" %%B in ('git branch --show-current') do set "CURRENT_BRANCH=%%B"
if not defined CURRENT_BRANCH (
    echo ERROR: No se puede publicar desde detached HEAD.
    exit /b 1
)

git fetch --quiet origin
if errorlevel 1 (
    echo ERROR: No se pudo actualizar la referencia del remoto origin.
    exit /b 1
)
for /f "delims=" %%H in ('git rev-parse HEAD') do set "HEAD_COMMIT=%%H"
for /f "delims=" %%H in ('git rev-parse "origin/%CURRENT_BRANCH%" 2^>nul') do set "REMOTE_COMMIT=%%H"
if not defined REMOTE_COMMIT (
    echo ERROR: No existe origin/%CURRENT_BRANCH%. Publica primero la rama actual.
    exit /b 1
)
if not "%HEAD_COMMIT%"=="%REMOTE_COMMIT%" (
    echo ERROR: La rama local y origin/%CURRENT_BRANCH% no apuntan al mismo commit.
    echo        Haz push o actualiza la rama antes de publicar la release.
    exit /b 1
)

git show-ref --verify --quiet "refs/tags/%TAG%"
if not errorlevel 1 (
    echo ERROR: El tag local %TAG% ya existe. No se modificará.
    exit /b 1
)
git ls-remote --exit-code --tags origin "refs/tags/%TAG%" >nul 2>&1
if not errorlevel 1 (
    echo ERROR: El tag remoto %TAG% ya existe. No se modificará.
    exit /b 1
)
gh release view "%TAG%" --repo "%REPO%" >nul 2>&1
if not errorlevel 1 (
    echo ERROR: La release %TAG% ya existe. No se modificará.
    exit /b 1
)

REM =====================================================================
REM  5. Mostrar resumen y pedir confirmación
REM =====================================================================
echo [5/7] Revisión final...
echo.
echo --- Notas de la release ---
type "%NOTES_FILE%"
echo.
echo --- Fin de las notas ---
echo.
echo =====================================================================
echo   Repositorio: %REPO%
echo   Rama:        %CURRENT_BRANCH%
echo   Commit:      %HEAD_COMMIT%
echo   Tag:         %TAG%
echo   Archivos:    %MODE_DESCRIPTION%
echo =====================================================================
echo.

if /I "%AUTO_CONFIRM%"=="s" goto confirmed
set "CONFIRM="
set /p "CONFIRM=Crear el tag y publicar esta release? (s/n): "
if /I not "%CONFIRM%"=="s" (
    echo Operación cancelada. No se creó ningún tag ni release.
    exit /b 0
)

:confirmed
REM =====================================================================
REM  6. Crear y subir el tag; publicar la release
REM =====================================================================
echo [6/7] Creando y publicando %TAG%...

git tag -a "%TAG%" -m "WhatsApp CAN %VERSION%"
if errorlevel 1 (
    echo ERROR: No se pudo crear el tag local %TAG%.
    exit /b 1
)
git push origin "refs/tags/%TAG%"
if errorlevel 1 (
    echo ERROR: No se pudo subir el tag. Se quitará únicamente el tag local recién creado.
    git tag -d "%TAG%" >nul 2>&1
    exit /b 1
)

if /I "%MODE%"=="installer" goto publish_with_installer
gh release create "%TAG%" "%ZIP_PATH%" "%ZIP_CHECKSUM_PATH%" --repo "%REPO%" --title "WhatsApp CAN %VERSION%" --notes-file "%NOTES_FILE%" --latest
if errorlevel 1 goto publish_failed
goto verify_release

:publish_with_installer
gh release create "%TAG%" "%ZIP_PATH%" "%ZIP_CHECKSUM_PATH%" "%INSTALLER_PATH%" "%INSTALLER_CHECKSUM_PATH%" --repo "%REPO%" --title "WhatsApp CAN %VERSION%" --notes-file "%NOTES_FILE%" --latest
if errorlevel 1 goto publish_failed

:verify_release
REM =====================================================================
REM  7. Verificar la release publicada
REM =====================================================================
echo [7/7] Verificando la publicación...

gh release view "%TAG%" --repo "%REPO%" --json isDraft --jq ".isDraft" | findstr /I /X "false" >nul
if errorlevel 1 goto verification_failed
gh release view "%TAG%" --repo "%REPO%" --json isPrerelease --jq ".isPrerelease" | findstr /I /X "false" >nul
if errorlevel 1 goto verification_failed
call :verify_asset "%ZIP_NAME%"
if errorlevel 1 goto verification_failed
call :verify_asset "%ZIP_CHECKSUM_NAME%"
if errorlevel 1 goto verification_failed
if /I "%MODE%"=="installer" (
    call :verify_asset "%INSTALLER_NAME%"
    if errorlevel 1 goto verification_failed
    call :verify_asset "%INSTALLER_CHECKSUM_NAME%"
    if errorlevel 1 goto verification_failed
)
for /f "usebackq delims=" %%U in (`gh release view "%TAG%" --repo "%REPO%" --json url --jq ".url"`) do set "RELEASE_URL=%%U"

echo.
echo =====================================================================
echo   Release publicada y verificada correctamente.
echo   Tag: %TAG%
echo   URL: %RELEASE_URL%
echo =====================================================================
echo.
exit /b 0

:verify_asset
gh release view "%TAG%" --repo "%REPO%" --json assets --jq ".assets[].name" | findstr /L /X /C:"%~1" >nul
exit /b %ERRORLEVEL%

:publish_failed
echo.
echo ERROR: GitHub no pudo crear la release.
echo        El tag %TAG% ya fue subido al remoto y no se eliminará automáticamente.
echo        Revisa el error anterior antes de volver a intentar.
exit /b 1

:verification_failed
echo.
echo ERROR: La release fue creada, pero la verificación final no pasó.
echo        Revísala manualmente con:
echo        gh release view %TAG% --repo %REPO%
exit /b 1

:need_build
echo.
echo por favor corre el script para realizar la compilación.
echo Ejecuta: powershell -ExecutionPolicy Bypass -File .\build_release.ps1
exit /b 1

:dirty_tree
echo ERROR: Hay cambios sin commit. Confírmalos o descártalos antes de publicar.
exit /b 1

:invalid_version
echo ERROR: Formato de versión inválido. Usa x.y.z, por ejemplo 1.0.5.
exit /b 2

:usage
echo.
echo Uso interactivo:
echo   publish_release.bat
echo.
echo Uso con argumentos:
echo   publish_release.bat VERSION zip
echo   publish_release.bat VERSION installer
echo   publish_release.bat VERSION zip --yes
echo   publish_release.bat VERSION installer --yes
echo.
echo Modos:
echo   zip        Publica el ZIP del programa y su firma SHA-256.
echo   installer  Publica también el instalador EXE y su firma SHA-256.
echo.
echo --yes omite únicamente la confirmación final. Las validaciones siempre se ejecutan.
exit /b 2
