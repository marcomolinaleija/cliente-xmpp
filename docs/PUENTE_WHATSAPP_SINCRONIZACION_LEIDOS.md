# Sincronizar lecturas hechas en WhatsApp oficial

## Estado

Este cambio está **publicado y activo en `marco-vps` desde el 14 de julio de 2026**. El cliente de
escritorio consume:

- `<displayed xmlns="urn:xmpp:chat-markers:0"/>` dentro de `carbon_sent` en chats 1 a 1.
- Publicaciones XEP-0490 `urn:xmpp:mds:displayed:0` en grupos.

La imagen vigente es:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v8
sha256:64811c17a2b12c90d0f0c4fb0e7654d5663031a7d351342c089945b4d9100fe3
```

También está publicada como `roster-sync-20260718`. `v7` se conserva como rollback inmediato sin
la sincronización automática ni la fusión de alias. `v6` se conserva sin la conservación de
timestamps incompletos. `audio-fix-20260714`/`v5` se conserva
como rollback anterior sin las correcciones de presencia. `read-sync-20260714`/`v4` conserva el primer
despliegue de lecturas, pero no debe usarse porque borra los adjuntos entrantes servidos localmente. La etiqueta
`puente-completo-20260713` y su alias `v3` se conservan como rollback anterior sin sincronización
de lecturas.

## Evidencia de la incidencia

El 13 de julio de 2026 se capturo una reproduccion real con dos recursos XMPP conectados:

1. Llego un mensaje individual de WhatsApp al cliente XMPP.
2. El `id` del stanza recibido coincidio con el `message_id` persistido en SQLite.
3. El usuario abrio y cerro el chat en WhatsApp oficial.
4. No llego ningun `carbon_sent`, marker XEP-0333, publicacion XEP-0490 ni actualizacion de inbox.
5. Al abrir un chat desde `cliente-xmpp`, el servidor si genero correctamente un
   `carbon_sent` con `<displayed/>`.

Esto descarta un problema de IDs en el cliente y confirma que los privilegios de mensaje
saliente de Prosody funcionan. La perdida ocurre antes de XMPP.

En la fuente actual de `slidge-whatsapp`, `whatsmeow` declara
`events.MarkChatAsRead`, pero `slidge_whatsapp/session.go` no incluye ese tipo en el `switch` de
`Session.handleEvent`. Solo `events.Receipt` se convierte en `EventReceipt`. Por eso una lectura
hecha desde otro dispositivo de WhatsApp se registra en el estado multidispositivo de WhatsApp,
pero nunca llega a `Session.on_wa_receipt()` en Python ni a Slidge.

## Cambio aplicado en slidge-whatsapp

La construcción parte del mismo checkout que formó la imagen anterior y conserva los parches de
menciones, stickers y reenvíos. El cambio pertenece a `slidge-whatsapp`, no a Slidge core. Para
reproducirlo se usa:

```bash
python tools/patch_slidge_whatsapp_read_sync.py RUTA_A_SLIDGE_WHATSAPP
```

`tools/Dockerfile.bridge-read-sync.patch` aplica ese script durante la construcción, copia
`tools/bridge_read_sync_event_test.go`, ejecuta `go test ./...` y sólo después instala la fuente
en la imagen. La base construida era `88b2f91` y el commit final de la fuente en la VPS es
`25431c4`.

### 1. Convertir `events.MarkChatAsRead` en `EventReceipt`

Agregar en `slidge_whatsapp/event.go` un conversor con estas reglas:

- Ignorar `Action == nil`.
- Ignorar `Action.Read == false`: XEP-0333 y XEP-0490 son monotonicos y no representan
  correctamente "marcar como no leido".
- Leer `Action.MessageRange.Messages`.
- Elegir la entrada valida mas reciente por `Timestamp` y usar `MessageKey.ID`.
- Crear el chat con `evt.JID`; es grupo cuando `evt.JID.Server == types.GroupServer`.
- Crear el actor con el JID/LID propios para que `Actor.IsMe` sea `true`.
- Emitir `ReceiptRead` con el timestamp del evento.
- Si no existe un ID valido, registrar un warning sin contenido del mensaje y no emitir nada.

Implementacion orientativa:

```go
func newMarkChatAsReadEvent(
    ctx context.Context,
    client *whatsmeow.Client,
    evt *events.MarkChatAsRead,
) (EventKind, *EventPayload) {
    action := evt.Action
    if action == nil || !action.GetRead() {
        return EventUnknown, nil
    }

    var messageID string
    var latestTimestamp int64
    if messageRange := action.GetMessageRange(); messageRange != nil {
        for _, message := range messageRange.GetMessages() {
            key := message.GetKey()
            if key == nil || key.GetID() == "" {
                continue
            }
            if messageID == "" || message.GetTimestamp() >= latestTimestamp {
                messageID = key.GetID()
                latestTimestamp = message.GetTimestamp()
            }
        }
    }
    if messageID == "" {
        client.Log.Warnf("Ignoring MarkChatAsRead without a message ID for %s", evt.JID)
        return EventUnknown, nil
    }

    chat := newChat(
        ctx,
        client,
        evt.JID,
        evt.JID.Server == types.GroupServer,
    )
    if chat.JID == "" {
        client.Log.Warnf("Ignoring MarkChatAsRead for unknown chat %s", evt.JID)
        return EventUnknown, nil
    }

    actor := newActor(
        ctx,
        client,
        client.Store.GetJID(),
        client.Store.GetLID(),
    )
    receipt := Receipt{
        Kind:       ReceiptRead,
        MessageIDs: []string{messageID},
        Actor:      actor,
        Chat:       chat,
        Timestamp:  evt.Timestamp.Unix(),
    }
    return EventReceipt, &EventPayload{Receipt: receipt}
}
```

No agregar un tipo nuevo a `EventKind` ni a `EventPayload`: reutilizar `EventReceipt` permite que
la ruta Python existente haga lo correcto:

```python
contact.displayed(legacy_msg_id=message_id, carbon=receipt.Actor.IsMe)
```

Para chats individuales, Slidge enviara el marker XEP-0333 como mensaje privilegiado del usuario
y Prosody lo reflejara a los otros recursos mediante carbons. Para grupos, el participante propio
publicara ademas el estado XEP-0490.

### 2. Conectar el evento en `Session.handleEvent`

En `slidge_whatsapp/session.go`, junto al caso de `events.Receipt`, agregar:

```go
case *events.MarkChatAsRead:
    s.propagateEvent(newMarkChatAsReadEvent(s.ctx, s.client, evt))
```

No llamar `client.MarkRead()` desde este handler. Esa funcion envia una lectura hacia WhatsApp y
crearia un bucle. Este evento ya describe una accion realizada en otro dispositivo; solo debe
reflejarse hacia XMPP.

### 3. Conservar los adjuntos servidos por Slidge

La primera imagen de lecturas adaptó el código a la ausencia de `NO_UPLOAD_METHOD`, pero dejó la
condición de limpieza con sentido inverso. En Slidge actual, `send_files` copia el adjunto a
`NO_UPLOAD_PATH` y actualiza `attachment.path` para apuntar a ese archivo persistente. El bucle
posterior lo eliminaba de inmediato y la URL anunciada devolvía HTTP 404.

El parche ahora omite toda limpieza cuando `NO_UPLOAD_PATH` está configurado. Para instalaciones
antiguas conserva también los archivos servidos mediante `NO_UPLOAD_METHOD == "symlink"`; sólo
elimina temporales cuando ninguna modalidad persistente está activa. No se corrige recreando la
base, borrando volúmenes ni cambiando el cliente de escritorio.

## Configuración requerida de Prosody

La instalación completa en servidores distintos de `marco-vps`, incluido ejabberd, respaldos,
validación, despliegue y rollback, se documenta en
`docs/PUENTE_WHATSAPP_OTROS_SERVIDORES.md`. Esta sección conserva el detalle específico de la
incidencia original de XEP-0490.

Los mensajes privilegiados ya funcionaban para chats individuales, pero XEP-0490 necesita además
privilegios PubSub. `marco-vps` usa ahora `prosodyim/prosody:0.12`; el módulo de privilegios
instalado no podía entregar IQ PubSub correctamente sobre la antigua imagen Prosody 0.11.9.

En `slidge_privileges.iq` deben existir:

```lua
["http://jabber.org/protocol/pubsub"] = "both";
["http://jabber.org/protocol/pubsub#owner"] = "set";
```

La configuración conserva el módulo HTTP Upload heredado con esta ruta explícita:

```lua
http_files_dir = "/var/lib/prosody/http_upload"
```

El cambio es reproducible e idempotente con:

```bash
python tools/patch_prosody_read_sync_privileges.py /opt/xmpp/prosody/config/prosody.cfg.lua
docker run --rm \
  -v /opt/xmpp/prosody/config:/etc/prosody:ro \
  -v /opt/xmpp/prosody/data:/var/lib/prosody:ro \
  -v /opt/xmpp/certs:/certs:ro \
  --entrypoint prosodyctl prosodyim/prosody:0.12 check config
```

Antes de producción se validó una copia de configuración y datos en una red Docker aislada, con
una instancia vacía del puente. El componente autenticó y recibió los privilegios sin reutilizar
la sesión real de WhatsApp.

## Pruebas requeridas

### Unitarias en Go

Cubrir al menos:

1. `Read=true` con un `MessageKey.ID` valido produce `EventReceipt`, `ReceiptRead`, actor propio y
   el chat correcto.
2. Con varias entradas elige la de timestamp mayor.
3. `Read=false` no emite evento.
4. Rango vacio o ID vacio no emite evento.
5. Un JID `g.us` se clasifica como grupo.

Ejecutar:

```bash
go test ./...
```

Como se reutilizan estructuras ya exportadas a Python, no deberia ser necesario ampliar las
bindings de gopy. La construccion completa de la imagen sigue siendo obligatoria para confirmarlo.

### Smoke test dentro de la imagen

Confirmar que el `switch`, el conversor y la corrección de adjuntos están presentes:

```bash
docker run --rm --entrypoint python \
  -v "$PWD/tools/smoke_bridge_read_sync_runtime.py:/tmp/smoke.py:ro" \
  IMAGEN_CANDIDATA /tmp/smoke.py

docker run --rm --entrypoint python \
  -v "$PWD/tools/smoke_bridge_attachment_persistence_runtime.py:/tmp/smoke.py:ro" \
  IMAGEN_CANDIDATA /tmp/smoke.py
```

### Prueba funcional obligatoria

1. Mantener `cliente-xmpp` conectado y visible en la lista de chats.
2. Recibir un mensaje nuevo en un chat individual y no abrirlo en el cliente.
3. Abrirlo desde WhatsApp oficial.
4. Confirmar que aparece un `carbon_sent` con `<displayed/>` y que el contador del cliente baja a
   cero sin F5, reconexion ni polling.
5. Repetir en un grupo y confirmar la publicacion XEP-0490.
6. Recibir otro mensaje inmediatamente despues del marker y confirmar que ese mensaje posterior
   permanece no leido.
7. Usar "marcar como no leido" en WhatsApp oficial y confirmar que el cliente no retrocede su
   horizonte de lectura.
8. Recibir una nota de voz nueva, confirmar que su URL devuelve HTTP 200, que el archivo sigue en
   `/opt/xmpp/slidge-attachments` y que `cliente-xmpp` lo descarga y reproduce localmente.

## Construccion y despliegue

La versión publicada es:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v8
```

En la VPS:

```bash
cd /opt/xmpp
cp -p compose.yml compose.yml.before-read-sync
# Seleccionar Prosody 0.12 y la imagen publicada del puente.
python RUTA_REPO/tools/patch_marco_vps_compose_read_sync.py \
  --bridge-image ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v8 \
  --automatic-roster-sync \
  compose.yml
docker compose config -q
docker compose pull prosody
docker compose pull slidge-whatsapp
docker compose up -d --no-deps --force-recreate prosody
docker compose up -d --no-deps --force-recreate slidge-whatsapp
docker inspect slidge-whatsapp --format 'running={{.State.Running}} restarts={{.RestartCount}} image={{.Config.Image}}'
docker logs --since 10m --tail 150 slidge-whatsapp
```

Detenerse si solicita QR o pierde la sesion. No ejecutar `docker compose down -v` ni borrar
`/opt/xmpp/slidge` o los adjuntos.

## Rollback

```bash
cd /opt/xmpp
cp -p compose.yml.before-read-sync compose.yml
docker compose config -q
docker compose up -d --no-deps --force-recreate slidge-whatsapp
```

En `marco-vps` existe además el respaldo completo
`/opt/xmpp/backups/read-sync-20260714/`, con `compose.yml.before`, la configuración de Prosody y
un archivo comprimido de sus datos. No ejecutar `docker compose down -v` ni borrar
`/opt/xmpp/slidge` o `/opt/xmpp/slidge-attachments` durante un rollback.

## Referencias

- XEP-0333, Displayed Markers: https://xmpp.org/extensions/xep-0333.html
- XEP-0490, Message Displayed Synchronization: https://xmpp.org/extensions/xep-0490.html
- Privilegios de slidge-whatsapp: https://slidge.im/docs/slidge-whatsapp/main/admin/privileges.html
- Evento `MarkChatAsRead` de whatsmeow:
  `vendor/go.mau.fi/whatsmeow/types/events/appstate.go`
