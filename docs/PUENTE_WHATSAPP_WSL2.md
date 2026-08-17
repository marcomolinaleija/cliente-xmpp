# Appliance local del puente de WhatsApp para WSL2

Este documento describe el appliance reproducible que instala Prosody y una imagen inicial de
`slidge-whatsapp` v19 dentro de una distribución WSL2 exclusiva. No modifica otras
distribuciones del usuario y no requiere Docker Desktop.

## Estado

La implementación reproducible vive en `tools/wsl-appliance/`.
Produce `WhatsAppCAN-Bridge-amd64.wsl`, genera secretos distintos por instalación y sólo publica
XMPP y adjuntos en `127.0.0.1`.

El ciclo de construcción, instalación limpia, apagado completo, reinicio y autenticación STARTTLS
se validó en Windows 11 x64 con WSL 2.6.1. La primera fase técnica también cubrió registro en el
gateway, vinculación QR, carga de chats, mensajes entrantes y salientes, reapertura después de 20
segundos, arranque automático con la distribución detenida y recuperación completa después de
reiniciar Windows. En ninguno de esos reinicios se volvió a solicitar registro ni QR.

La arquitectura es:

```text
cliente-xmpp en Windows
  -> 127.0.0.1:5222, XMPP local
     -> Prosody 0.12 nativo
        -> Slidge WhatsApp v19 en Podman
  -> 127.0.0.1:5280, subida XEP-0363
     -> http_file_share de Prosody
  -> 127.0.0.1:8080, adjuntos locales
     -> nginx nativo
```

Los datos persistentes quedan dentro del VHDX de la distribución:

- `/var/lib/whatsapp-can-bridge/slidge`: sesión y base de Slidge.
- `/var/lib/whatsapp-can-bridge/attachments`: adjuntos servidos al cliente local.
- `/var/lib/prosody`: metadatos y archivos temporales del servicio XEP-0363.
- `/etc/whatsapp-can-bridge`: secretos generados en el primer arranque.

## Requisitos actuales

- Windows x64 con WSL 2.4.4 o posterior.
- Espacio suficiente para Ubuntu 24.04, Podman y la imagen v19.
- Conexión a Internet sólo durante la construcción del artefacto. La instalación final carga la
  imagen v19 incluida en el `.wsl`.
- Puertos locales 5222, 5280 y 8080 libres. Sólo puede estar activa una instancia del appliance.

La imagen contiene un binding nativo x86-64; ARM64 queda fuera de este prototipo.

## Instalador para usuarios finales

El instalador de WhatsApp CAN presenta una página titulada `Forma de conexión` con dos opciones:

- `Puente local en este equipo`, seleccionada por defecto. Requiere WSL2 y descarga el appliance
  sólo al confirmar la instalación.
- `Servidor XMPP o VPS`. Instala exclusivamente el cliente de Windows y no descarga ni modifica
  ninguna distribución WSL.

El appliance no se incrusta en el EXE. Vive en la release dedicada e inmutable
`wsl-appliance-v1.0.0`; `tools/wsl-appliance/release-manifest.json` fija su URL, tamaño y SHA-256.
`build_release.ps1` copia esos valores al instalador y rechaza la compilación si el artefacto local
no coincide. Inno Setup vuelve a comprobar el SHA-256 durante la descarga antes de ejecutar
`install-appliance.ps1`.

El modo elegido se guarda en el cliente al terminar. Una reinstalación recuerda la selección
anterior y también admite `/CONNECTIONMODE=local` o `/CONNECTIONMODE=remote` para pruebas
automatizadas. Desinstalar el cliente no elimina automáticamente `WhatsAppCAN-Bridge`, porque esa
distribución contiene la sesión y los datos del usuario; se elimina sólo mediante el flujo con
respaldo y confirmación descrito más adelante.

## Construir

Desde PowerShell, sin permisos administrativos:

```powershell
.\tools\wsl-appliance\build-appliance.ps1
```

El script:

1. Descarga la raíz oficial Ubuntu 24.04 WSL y valida su SHA-256 fijado.
2. Crea una distribución temporal con un nombre aleatorio controlado.
3. Instala Prosody, módulos comunitarios, nginx y Podman.
4. Descarga v19 por su digest publicado, la guarda como archivo OCI y fija el ID obtenido tras
   volver a importar ese mismo OCI.
5. Exporta `dist/wsl/WhatsAppCAN-Bridge-amd64.wsl` y su `.sha256`.
6. Elimina únicamente la distribución temporal creada por ese build.

Antes de exportar, deja `/etc/machine-id` vacío —no ausente— y enlaza
`/var/lib/dbus/machine-id`. Así cada instalación obtiene una identidad propia sin activar el
asistente interactivo `systemd-firstboot`, que bloquearía el arranque no interactivo de WSL.

`-SkipBridgeImage` existe sólo para iterar rápidamente sobre el rootfs. Un artefacto construido
con esa opción no es instalable para usuario final.

## Instalar una prueba

```powershell
.\tools\wsl-appliance\install-appliance.ps1 `
  -PackagePath .\dist\wsl\WhatsAppCAN-Bridge-amd64.wsl
```

La instalación usa el nombre `WhatsAppCAN-Bridge`, genera la cuenta local y guarda el contrato de
conexión en `%LOCALAPPDATA%\WhatsAppCAN\bridge-connection.json`, restringido al usuario actual.
Antes de registrar la distribución rechaza la instalación si 5222, 5280 u 8080 ya están ocupados.
Durante este prototipo ese JSON contiene la contraseña necesaria para configurar el cliente. La
primera apertura de WhatsApp CAN la mueve al almacén de credenciales de Windows y sólo entonces
elimina el campo del archivo, conservando la ACL restrictiva. Las aperturas posteriores obtienen
el secreto directamente de Windows.

Si la configuración se interrumpe después de registrar la distribución, se reanuda sin reemplazar
el VHDX ni generar otros secretos:

```powershell
.\tools\wsl-appliance\install-appliance.ps1 `
  -PackagePath .\dist\wsl\WhatsAppCAN-Bridge-amd64.wsl -Resume
```

### Migrar una distribución anterior

El Setup de la versión 1.1 usa `-InstallOrResume` y detecta automáticamente si la distribución
existente carece del actualizador o de `http_file_share`. En ese caso realiza una única migración;
actualizar solamente el programa de Windows no habría reemplazado el rootfs anterior.

Antes de desregistrar la distribución antigua, el instalador detiene sus servicios y crea dos
respaldos protegidos con SHA-256: una exportación completa recuperable y un archivo selectivo con
credenciales, CA, sesión de WhatsApp, adjuntos y datos de Prosody. Sólo entonces instala el nuevo
rootfs, restaura esos datos, regenera la configuración y ejecuta los smoke tests de XMPP, Slidge y
XEP-0363. El usuario conserva su contraseña, certificado y sesión, por lo que no debería escanear
otro QR.

Un journal en `%LOCALAPPDATA%\WhatsAppCAN\migration-backups` permite reconocer una interrupción. Si
la migración falla después del reemplazo, el instalador elimina únicamente la candidata y reimporta
la exportación completa anterior. Los respaldos no se borran al terminar correctamente. La opción
manual `-Resume` continúa destinada a una instalación incompleta y no autoriza reemplazar una
distribución heredada; para la migración segura se usa `-InstallOrResume`.

Para usuarios que ya instalaron la versión 1.0 se publica además
`actualizar-puente-local.ps1` como asset de la release 1.1. Deben descargar únicamente ese script,
cerrar WhatsApp CAN y ejecutarlo desde PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\actualizar-puente-local.ps1
```

La fuente usa mensajes en español y UTF-8 con BOM para Windows PowerShell 5.1. El asset público es
un envoltorio ASCII autocontenido que decodifica esa fuente internamente, por lo que funciona tanto
como archivo descargado como mediante `irm URL | iex` sin convertir el BOM ni los acentos en
mojibake. Solicita escribir `ACTUALIZAR`, descarga desde GitHub tanto el `.wsl` como el instalador
fijado al tag 1.1, verifica tamaño y SHA-256 antes de ejecutar y no imprime credenciales. Si ya
encuentra una distribución 1.1 no cambia nada. En caso de fallo conserva las descargas verificadas
para reintentar; al tener éxito las elimina salvo que se use `-ConservarDescargas`.

Los valores no sensibles esperados son:

```json
{
  "jid": "whatsappcan@xmpp.whatsappcan.local",
  "host": "127.0.0.1",
  "port": 5222,
  "use_tls": true,
  "ca_file": "C:\\Users\\usuario\\AppData\\Local\\WhatsAppCAN\\bridge-ca.crt"
}
```

Cada instalación genera una CA y un certificado de servidor propios. El instalador exporta
solamente la CA pública y el cliente la usa como ancla específica para STARTTLS; no la agrega al
almacén raíz global de Windows. Los puertos siguen limitados a loopback y no deben abrirse a la
LAN.

## Operar y diagnosticar

```powershell
.\tools\wsl-appliance\manage-appliance.ps1 status
.\tools\wsl-appliance\manage-appliance.ps1 smoke
.\tools\wsl-appliance\manage-appliance.ps1 logs
.\tools\wsl-appliance\manage-appliance.ps1 restart
.\tools\wsl-appliance\manage-appliance.ps1 update
```

El mismo flujo está incorporado dentro de la distribución y puede invocarse sin una copia del
repositorio:

```powershell
wsl.exe -d WhatsAppCAN-Bridge -u root -- /usr/local/sbin/whatsapp-can-bridge update
```

La autenticación real desde el código del cliente se valida, dentro del entorno Conda `XMPP`,
sin imprimir la contraseña:

```powershell
conda run -n XMPP python .\tools\wsl-appliance\smoke_local_xmpp.py `
  "$env:LOCALAPPDATA\WhatsAppCAN\bridge-connection.json"
```

El simple uso de `wsl -d WhatsAppCAN-Bridge` despierta la distribución y systemd inicia los
servicios habilitados. Los servicios systemd por sí solos no impiden que WSL vuelva a detener la
distribución. Por eso, durante una sesión local, el cliente mantiene abierto el comando
`whatsapp-can-bridge keepalive`; al cerrar la aplicación cierra su entrada estándar y permite que
WSL termine normalmente.

### Actualizar solamente el puente

Desde la versión 1.1 del appliance, la imagen activa de Slidge no está fijada dentro de la unidad
de systemd. El appliance consulta el manifiesto estable HTTPS de este repositorio, acepta únicamente
imágenes `ghcr.io/marcomolinaleija/cliente-xmpp-bridge:vN` con digest SHA-256 y ejecuta el ID local
inmutable que produjo esa descarga. Para actualizar no es necesario reinstalar ni descargar otro
archivo `.wsl`:

```powershell
.\tools\wsl-appliance\manage-appliance.ps1 update
```

El actualizador descarga primero la imagen sin interrumpir el servicio. Después detiene solamente
Slidge, conserva un respaldo consistente de `/var/lib/whatsapp-can-bridge/slidge`, activa la imagen
nueva y ejecuta el smoke test. Si la validación falla, restaura automáticamente tanto la imagen como
la base anterior y vuelve a iniciar el puente. Los respaldos se conservan con permisos restringidos
en `/var/lib/whatsapp-can-bridge/update-backups/`; no incluyen adjuntos porque una actualización de
imagen no los modifica.

El canal estable vive en `tools/wsl-appliance/bridge-update-manifest.json`. Sólo debe cambiarse
después de publicar y validar la imagen indicada. El manifiesto contiene etiqueta y digest; el
actualizador rechaza repositorios distintos, etiquetas sin formato `vN`, digests inválidos y URLs
que no usen HTTPS.

### Archivos y audios salientes

Prosody ofrece `upload.xmpp.whatsappcan.local` mediante XEP-0363. Los slots anuncian únicamente
`http://127.0.0.1:5280/file_share/`, por lo que tanto el cliente de Windows como Slidge pueden leer
el contenido sin exponerlo a la LAN. Cada archivo puede medir como máximo 200 MiB, la cuota diaria
de la cuenta local es de 2 GiB y los archivos temporales caducan a los siete días. WhatsApp recibe
el contenido como multimedia nativa; el enlace loopback sólo transporta el archivo entre el cliente
y el puente local.

La validación reproducible de autenticación, descubrimiento, slot, PUT, GET e imposición del límite
se ejecuta con la contraseña temporalmente en una variable de entorno:

```powershell
$env:WHATSAPP_CAN_LOCAL_SMOKE_PASSWORD = "<contraseña local>"
conda run -n XMPP python .\tools\wsl-appliance\smoke_local_upload.py `
  --jid whatsappcan@xmpp.whatsappcan.local `
  --ca-file "$env:LOCALAPPDATA\WhatsAppCAN\bridge-ca.crt"
Remove-Item Env:WHATSAPP_CAN_LOCAL_SMOKE_PASSWORD
```

Antes de abrir XMPP, el cliente ejecuta `start` y `smoke` en un ejecutor dedicado, valida loopback,
STARTTLS y la CA, y espera el marcador real `Slidge has successfully started`. Ese trabajo nunca
bloquea el hilo wx. La comprobación compara el contador de reinicios al principio y al final: un
reinicio histórico no bloquea el arranque, pero uno ocurrido durante el smoke test sí.

## Cliente híbrido: local o servidor

Instalar el appliance no obliga a usarlo. WhatsApp CAN conserva perfiles independientes para:

- `Puente local (WSL2)`, con el JID, loopback y CA generados por el instalador.
- `Servidor XMPP`, con el servidor remoto o personalizado que configure el usuario.

El selector de Configuración guarda el modo para la siguiente apertura. Cuando la aplicación está
desconectada, el selector de la pantalla de conexión permite cambiar inmediatamente. Cada perfil
conserva sus propios ajustes y la contraseña continúa guardada por JID en el almacén seguro de
Windows. La primera migración puede recuperar el perfil remoto respaldado en
`%LOCALAPPDATA%\WhatsAppCAN\migration-backups\settings-before-local-bridge.json`.

## Respaldo y desinstalación segura

Un respaldo completo exporta el rootfs, la sesión de WhatsApp y los adjuntos:

```powershell
.\tools\wsl-appliance\manage-appliance.ps1 backup `
  -BackupPath D:\Respaldos\WhatsAppCAN-Bridge.tar.gz
```

El gestor detiene los tres servicios de forma ordenada, exporta con la distribución apagada,
genera el `.sha256` y restringe ambos archivos al usuario actual. La distribución queda detenida;
`manage-appliance.ps1 start` vuelve a levantarla.

La desinstalación siempre exige respaldo y repetir exactamente el nombre de la distribución:

```powershell
.\tools\wsl-appliance\manage-appliance.ps1 uninstall `
  -BackupPath D:\Respaldos\WhatsAppCAN-Bridge-final.tar.gz `
  -ConfirmDistroName WhatsAppCAN-Bridge
```

`wsl --unregister` destruye el VHDX registrado. No se debe ocultar esta confirmación ni eliminar
automáticamente la distribución después de un fallo de instalación.

## Siguientes pasos

- Validar construcción e instalación en Windows 11 limpio y Windows 10 22H2 con WSL actualizado.
- Validar en una instalación desechable la actualización por manifiesto y su rollback automático.
- Probar llamadas, grupos y multimedia con una cuenta desechable.
- Validar suspensión y reanudación de Windows, además del apagado de WSL ya cubierto.

## Fuentes de plataforma

- Distribuciones WSL personalizadas:
  https://learn.microsoft.com/windows/wsl/build-custom-distro
- systemd en WSL:
  https://learn.microsoft.com/windows/wsl/systemd
- raíz Ubuntu 24.04 para WSL:
  https://cloud-images.ubuntu.com/wsl/releases/noble/current/
