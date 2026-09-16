# Puente WhatsApp v28 — sin autoentrada a grupos nuevos

## Estado

La corrección fue validada en la VPS `rayoscompany` y publicada como:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v28@sha256:4b9ba26457cafa0ffe2f4c19a503d2288445d8f7b40bc0fab9133a23e01eca35
```

La imagen conserva la base funcional de v27 y cambia la publicación de grupos nuevos para usar
`auto_join=False`. Los bookmarks existentes conservan su preferencia previa.

## Qué cambia

- Un grupo nuevo descubierto por WhatsApp se publica sin autoentrada XMPP.
- Una invitación puede seguir notificándose, pero no provoca por sí sola la entrada al MUC.
- El cliente interpreta el atributo `autojoin` del bookmark y sólo entra cuando está habilitado.
- Los grupos guardados en caché se monitorean sin entrar; abrir el grupo o enviar un mensaje sigue
  siendo una acción explícita y conserva la unión necesaria.

## Validación

- Build del bridge: smoke runtime `no-auto-join bridge runtime smoke: ok`.
- Imagen publicada: digest anterior.
- VPS: contenedor `slidge-whatsapp` activo con v28, marcador de arranque de Slidge presente,
  `PRAGMA quick_check=ok` y sin `Traceback`, `FATAL`, `panic` ni `ERROR` desde el arranque.
- Prosody y `slidge-attachments` no fueron recreados.
- Se conservó un respaldo de Compose y de `/opt/xmpp/slidge` antes del cambio.

## Nota de construcción

El digest v27 ya estaba cerca del límite de capas del Docker Engine de la VPS. Para evitar el error
`max depth exceeded`, v28 se construyó a partir de un rootfs exportado de la imagen v27 exacta,
aplanado y verificado antes de aplicar el parche. El resultado publicado tiene una configuración
explícita equivalente para `slidge-whatsapp`, `/var/lib/slidge`, usuario `slidge` y `PATH` de la
venv.
