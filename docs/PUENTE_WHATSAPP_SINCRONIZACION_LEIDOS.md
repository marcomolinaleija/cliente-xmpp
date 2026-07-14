# Sincronizar lecturas hechas en WhatsApp oficial

## Estado

Este cambio esta **pendiente en el puente**. El cliente de escritorio ya esta preparado para
consumir:

- `<displayed xmlns="urn:xmpp:chat-markers:0"/>` dentro de `carbon_sent` en chats 1 a 1.
- Publicaciones XEP-0490 `urn:xmpp:mds:displayed:0` en grupos.

La imagen activa al investigar el problema era:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:puente-completo-20260713
```

No reutilizar esa etiqueta para el arreglo. Publicar una etiqueta nueva y conservar la anterior
para rollback.

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

## Cambio requerido en slidge-whatsapp

Trabajar sobre el mismo checkout usado para construir la imagen activa y conservar los parches
de menciones, stickers y reenvios. El cambio pertenece a `slidge-whatsapp`, no a Slidge core.

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

Confirmar que el `switch` y el conversor estan presentes:

```bash
python - <<'PY'
from pathlib import Path

root = Path("/build/slidge_whatsapp")
session_go = (root / "session.go").read_text()
event_go = (root / "event.go").read_text()
assert "case *events.MarkChatAsRead:" in session_go
assert "newMarkChatAsReadEvent" in event_go
assert "ReceiptRead" in event_go
print("MarkChatAsRead bridge patch present")
PY
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

## Construccion y despliegue

Aplicar este cambio ademas de los parches que ya forman la imagen completa. Publicar una etiqueta
nueva, por ejemplo:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:read-sync-YYYYMMDD
```

En la VPS:

```bash
cd /opt/xmpp
cp -p compose.yml compose.yml.before-read-sync
# Cambiar solo la imagen de slidge-whatsapp por la etiqueta nueva.
docker compose config -q
docker compose pull slidge-whatsapp
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

La imagen `puente-completo-20260713` debe permanecer disponible hasta terminar la prueba
funcional.

## Referencias

- XEP-0333, Displayed Markers: https://xmpp.org/extensions/xep-0333.html
- XEP-0490, Message Displayed Synchronization: https://xmpp.org/extensions/xep-0490.html
- Privilegios de slidge-whatsapp: https://slidge.im/docs/slidge-whatsapp/main/admin/privileges.html
- Evento `MarkChatAsRead` de whatsmeow:
  `vendor/go.mau.fi/whatsmeow/types/events/appstate.go`
