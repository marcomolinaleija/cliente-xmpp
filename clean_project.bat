@echo off
setlocal

title Limpiador de Proyecto whatsapp_CAN

echo Limpiando artefactos de PyInstaller...
if exist "build" (
    echo  - Eliminando carpeta 'build'...
    rmdir /s /q "build"
)
if exist "dist" (
    echo  - Eliminando carpeta 'dist'...
    rmdir /s /q "dist"
)

echo.
echo Limpiando cache de Python (__pycache__)... 
for /d /r . %%d in (__pycache__) do (
    if exist "%%d" (
        echo  - Eliminando %%d
        rmdir /s /q "%%d"
    )
)

echo.
echo Eliminando archivos de log (*.log)...
for /r %%f in (*.log) do (
    echo  - Eliminando %%f
    del /q "%%f"
)

echo.
echo Limpieza completada.
pause
endlocal
