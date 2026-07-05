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
