# Appliance local del puente de WhatsApp para WSL2

Este documento describe el prototipo reproducible que instala Prosody y
`slidge-whatsapp` v14 dentro de una distribución WSL2 exclusiva. No modifica otras
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
        -> Slidge WhatsApp v14 en Podman
  -> 127.0.0.1:8080, adjuntos locales
     -> nginx nativo
```

Los datos persistentes quedan dentro del VHDX de la distribución:

- `/var/lib/whatsapp-can-bridge/slidge`: sesión y base de Slidge.
- `/var/lib/whatsapp-can-bridge/attachments`: adjuntos servidos al cliente local.
- `/etc/whatsapp-can-bridge`: secretos generados en el primer arranque.

## Requisitos actuales

- Windows x64 con WSL 2.4.4 o posterior.
- Espacio suficiente para Ubuntu 24.04, Podman y la imagen v14.
- Conexión a Internet sólo durante la construcción del artefacto. La instalación final carga la
  imagen v14 incluida en el `.wsl`.
- Puertos locales 5222 y 8080 libres. Sólo puede estar activa una instancia del appliance.

La v14 contiene un binding nativo x86-64; ARM64 queda fuera de este prototipo.

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
4. Descarga v14 por su digest publicado, la guarda como archivo OCI y fija el ID obtenido tras
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
Antes de registrar la distribución rechaza la instalación si 5222 u 8080 ya están ocupados.
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
- Implementar actualización de la imagen por digest con backup, smoke test y rollback.
- Probar llamadas, grupos y multimedia con una cuenta desechable.
- Validar suspensión y reanudación de Windows, además del apagado de WSL ya cubierto.

## Fuentes de plataforma

- Distribuciones WSL personalizadas:
  https://learn.microsoft.com/windows/wsl/build-custom-distro
- systemd en WSL:
  https://learn.microsoft.com/windows/wsl/systemd
- raíz Ubuntu 24.04 para WSL:
  https://cloud-images.ubuntu.com/wsl/releases/noble/current/
