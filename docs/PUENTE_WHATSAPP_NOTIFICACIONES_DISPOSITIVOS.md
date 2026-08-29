# Notificaciones simultáneas en WhatsApp oficial y cliente XMPP

## Estado publicado

El 24 de agosto de 2026 se reprodujo en `marco-vps` que el teléfono oficial deja de emitir sonido
mientras el bridge mantiene WhatsMeow en `types.PresenceAvailable`. Detener sólo
`slidge-whatsapp` restauraba inmediatamente el sonido.

El 25 de agosto de 2026 se publicó y desplegó en `marco-vps`:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v22
sha256:b86a537631557da55f221262e7c5e6579ea805d1747a44c6730f526b4042ce19
```

`v22` parte exactamente de:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v21@sha256:688dbe86ab6d07f7f99b1901ea58744f6488cbfa76f4e4e2c005ad7a86a14248
```

La imagen publicada resuelve al mismo ID local de la candidata validada:
`sha256:294211184acab58951608bc1078a4343aa7ad12e8f2c2d0c9f3fff97dc1efc02`.
`/opt/xmpp/compose.yml` fija el digest de `v22`; el override usado durante la prueba ya no forma
parte del arranque estable.

## Cambio probado

`tools/patch_slidge_whatsapp_passive_presence.py` modifica las tres rutas de presencia de
`session.go`: las dos rutas de conexión y `Session.SendPresence`. Todas envían
`types.PresenceUnavailable`, aunque XMPP solicite `PresenceAvailable`. La sesión permanece
conectada, la cola lógica de refresco se conserva y los marcadores explícitos siguen usando
`MarkRead` sin cambios.

El Dockerfile ejecuta la prueba Go normal, el detector de carreras, recompila `_whatsapp.so` y
ejecuta un smoke test del runtime. La implementación está en:

- `tools/Dockerfile.bridge-passive-presence-v22`.
- `tools/patch_slidge_whatsapp_passive_presence.py`.
- `tools/bridge_passive_presence_test.go`.
- `tools/smoke_bridge_passive_presence_runtime.py`.
- `tests/test_patch_slidge_passive_presence.py`.

## Resultado funcional

- El teléfono oficial volvió a recibir mensajes con sonido.
- `cliente-xmpp` continuó recibiendo y notificando mensajes sin tener el foco.
- Una demora inmediatamente posterior a la recreación fue transitoria.
- Un monitor de SQLite, sin leer cuerpos ni identidades, registró mensajes nuevos a las
  `23:34:10` y `23:34:41` mientras la ventana permanecía en segundo plano.
- El contenedor autenticó con `Login success` y quedó con cero reinicios. Los logs conservaron un
  error administrativo de QR expirado y el cierre EOF del WebSocket anterior; no hubo panic ni
  caída de la sesión que recibió los mensajes de prueba.

Como seguimiento falta validar recepción de `composing`, grabación de audio, presencia y última
conexión. WhatsMeow documenta que algunas de esas señales requieren anunciar presencia disponible.

## Rollback

El respaldo previo está en `/opt/xmpp/backups/slidge-pre-v22-20260825-163354.tar.zst` y el Compose
anterior en `/opt/xmpp/backups/compose-pre-v22-20260825-163354.yml`. Para volver a `v21`, restaura
ese Compose y recrea únicamente el bridge:

```bash
cd /opt/xmpp
cp -p backups/compose-pre-v22-20260825-163354.yml compose.yml
docker compose config -q
docker compose -f compose.yml up -d --no-deps --force-recreate slidge-whatsapp
```

No borres volúmenes, bases SQLite ni la sesión de WhatsApp.
