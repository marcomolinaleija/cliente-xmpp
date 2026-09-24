# Cliente XMPP

Cliente de escritorio para Windows pensado como interfaz propia sobre un bridge XMPP de WhatsApp.

## Objetivo inicial

- Conectar con una cuenta XMPP existente.
- Leer contactos y chats expuestos por el bridge.
- Enviar y recibir mensajes 1 a 1.
- Explorar como el bridge representa grupos, adjuntos e historial.

## Stack tentativo

- Python
- wxPython para la interfaz nativa
- slixmpp para la conexion XMPP

## Estructura

```txt
cliente_xmpp/
  app/       arranque de la aplicacion
  config/    lectura y escritura de ajustes locales
  models/    datos compartidos como chats y mensajes
  ui/        ventanas y paneles wx
  xmpp/      conexion XMPP, eventos y envio de mensajes
```

## Desarrollo local

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m cliente_xmpp.app.main
```

La configuracion local se guarda en `%USERPROFILE%\.cliente-xmpp\settings.json`.

## Compilacion de Windows

La configuracion reproducible de PyInstaller vive en `WhatsApp-CAN.spec`. El build necesita el
entorno Conda `XMPP`, los dos DLL de `cliente_xmpp/lib` y `ffprobe` disponible en `PATH`.

```powershell
conda activate XMPP
python -m pip install -e ".[build]"
.\build_release.ps1
```

Al iniciar, elige `1` para generar sólo el ZIP y su SHA-256 o `2` para añadir el instalador.
En una ejecución no interactiva, usa `.\build_release.ps1 -ReleaseMode zip` o
`.\build_release.ps1 -ReleaseMode installer`.
El modo ZIP no modifica instaladores anteriores; al publicar esa compilación elige también el
modo `zip` de `publish_release.bat`.

La aplicacion se genera como una distribucion `onedir` en
`dist/WhatsApp-CAN/WhatsApp-CAN.exe`, junto con `update.exe` y `_internal`. Ambos ejecutables
son de ventana y no abren una consola al iniciar. El ZIP para actualizaciones y su SHA-256 se
generan bajo `release/`; el instalador de Inno Setup y su SHA-256 sólo se generan en el modo
completo. `publish_release.bat` publica por separado el ZIP o el paquete completo. La configuración
del puente local del instalador se describe en
[`docs/PUENTE_WHATSAPP_WSL2.md`](docs/PUENTE_WHATSAPP_WSL2.md).

## Configuracion y notificaciones de Windows

Al conectar la cuenta, el boton `Configuracion` de la cabecera abre una pantalla separada. Desde
ahi se puede:

- Activar o desactivar las notificaciones nativas de Windows.
- Ocultar el contenido del mensaje por privacidad.
- Pedir un anuncio directo adicional de NVDA si Windows no lo anuncia como se espera.
- Configurar los sonidos del chat abierto y de mensajes enviados.
- Dejar la ventana en la bandeja del sistema al usar `Alt+F4` y restaurarla desde su icono.
- Enviar una notificacion de prueba.

La opcion de bandeja esta desactivada por defecto para conservar el cierre habitual en
instalaciones existentes. El menu contextual del icono permite mostrar la ventana o salir
completamente de la aplicacion.

Las notificaciones se muestran solo para mensajes nuevos recibidos en vivo. Respetan los chats
silenciados y no aparecen si el usuario ya esta leyendo ese chat en la ventana activa. Al pulsar
la notificacion o `Responder`, el cliente abre el chat y enfoca el compositor; la accion `Marcar
como leido` actualiza el cliente y envia el marcador XMPP habitual.

La implementacion es completamente local al cliente y no requiere cambios en el puente. Los
detalles para mantenimiento y validacion estan en
[`docs/NOTIFICACIONES_WINDOWS.md`](docs/NOTIFICACIONES_WINDOWS.md).
