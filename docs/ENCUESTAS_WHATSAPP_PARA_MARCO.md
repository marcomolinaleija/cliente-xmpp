# Encuestas nativas de WhatsApp: cambio requerido en el puente

## Estado y objetivo de v16

La imagen quedó publicada el 11 de agosto de 2026 como:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v16@sha256:5c1e27f751218c0e3e06cb611f2b2942c8886a7ee04a744fbd8d4fbbf1cc3dd6
```

Se reconstruyó desde `main` en `f11c558`, usando la base v15 fijada que se documenta abajo.
Durante la construcción pasaron las pruebas Go normales y con `-race`, la recompilación del
binding compartido y el smoke test Python. Después de publicar, la imagen se descargó de GHCR por
su digest y el smoke test volvió a pasar. Los artefactos críticos coinciden byte por byte con la
imagen diagnóstica que se probó en `marco-vps`; publicar v16 no requirió reiniciar el servicio.

El cliente reconoce el contrato XMPP privado `urn:marco-ml:whatsapp:poll:0`, conserva encuestas y
últimos votos conocidos en SQLite, muestra totales en el mensaje y ofrece **Votar en encuesta...**
y **Ver resultados de encuesta...** en su menú contextual.

La imagen actual del puente reconoce `PollCreationMessage`, pero lo convierte a este texto de
compatibilidad antes de enviarlo por XMPP:

```text
🗳 Título
☐ Opción 1
☐ Opción 2
```

La imagen v16 debe dejar de descartar `PollUpdateMessage`: WhatsMeow lo descifra con
`DecryptPollVote` y entrega hashes SHA-256 de las opciones seleccionadas. Esos hashes sirven para
relacionar el voto con las opciones ya conocidas, pero no sustituyen el secreto criptográfico que
WhatsApp conserva en su almacén local. No se debe intentar votar enviando un mensaje de texto, una
reacción ni construyendo el payload cifrado desde el cliente.

Este cambio mantiene el texto anterior para clientes XMPP que aún no soporten encuestas y añade
metadatos sólo para el cliente actualizado. No toca cuentas, QR, sesiones vinculadas, roster,
Prosody ni las bases de datos de Slidge.

La base exacta es `ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v15` con digest
`sha256:c0431c164ba1f0e1ef6490fe41e86cef550ceec517ec876feb9b8b0fe2264307`. La construcción de v16
debe fijar ambos; no se acepta una etiqueta mutable sin digest.

## Contrato XMPP

El puente debe añadir al mensaje XMPP de creación:

```xml
<poll xmlns="urn:marco-ml:whatsapp:poll:0"
      id="ID-WHATSAPP-DE-LA-ENCUESTA"
      title="Pregunta"
      creator="numero@s.whatsapp.net"
      creator-lid="identidad@lid"
      creator-is-me="false"
      max-selections="1"
      selection-mode="single">
  <option>Primera opción</option>
  <option>Segunda opción</option>
</poll>
```

Los atributos `id`, `title`, `creator` y al menos una opción son obligatorios. En grupos debe
conservarse también `creator-lid`: WhatsApp usa esa identidad al cifrar votos. `max-selections`
se deriva de `SelectableOptionsCount` y de la variante del mensaje. WhatsApp usa la variante V3
para voto único: allí un cero se interpreta como `1`. En la variante V1 de voto múltiple, cero
significa que pueden marcarse todas las opciones. Un valor positivo explícito se conserva como el
límite en cualquier variante.
`selection-mode` es obligatorio en v16 y vale `single` o `multiple`. Si falta —por ejemplo, en
una stanza o caché creada durante los diagnósticos anteriores— el cliente asume `single` para no
habilitar votos múltiples accidentalmente.

Cuando el usuario elige una opción, el cliente envía al mismo contacto o sala:

```xml
<vote xmlns="urn:marco-ml:whatsapp:poll:0"
      id="ID-WHATSAPP-DE-LA-ENCUESTA"
      creator="numero@s.whatsapp.net"
      creator-lid="identidad@lid"
      creator-is-me="false">
  <option>Segunda opción</option>
</vote>
```

No incluye cuerpo de texto. El puente debe consumir esta extensión antes del manejador genérico de
mensajes y no debe reenviarla como una conversación normal.

Cada voto recibido o modificado se transporta así:

```xml
<poll-update xmlns="urn:marco-ml:whatsapp:poll:0"
             id="ID-WHATSAPP-DE-LA-ENCUESTA"
             voter="numero@s.whatsapp.net"
             voter-lid="identidad@lid"
             voter-is-me="false">
  <option hash="SHA256-HEX-DE-LA-OPCION"/>
</poll-update>
```

El `id` referencia la creación, no el evento de voto. Un evento posterior del mismo votante
reemplaza completamente su selección anterior; jamás se suma como otro voto. Cero opciones retira
su voto. El cliente identifica primero a la propia cuenta, después por LID y finalmente por JID.
Las actualizaciones no crean mensajes visibles, previews, no leídos ni notificaciones: mutan la
encuesta original y se archivan para poder reconstruir resultados desde MAM.

Después de que `Whatsmeow.SendMessage` devuelva aceptación del servidor de WhatsApp, el puente
emite al mismo cliente un `poll-update` con `voter-is-me="true"` y los hashes de la selección. El
cliente no modifica el voto propio al hacer clic: espera este evento. La confirmación demuestra
que WhatsApp aceptó el envío; no pretende ser un recibo de visualización en el teléfono de otra
persona. Si el envío falla, el puente no emite la actualización y el resultado visible permanece
sin cambios.

## Presentación y accesibilidad

El cuerpo visible conserva pregunta y opciones. Cada opción muestra su total y `☑` cuando forma
parte de la última selección propia; al final se indica cuántas personas han votado. El menú
**Ver resultados de encuesta...** presenta por opción el total y los nombres conocidos de los
votantes, usando `Tú` para la cuenta propia. Un voto desconocido conserva su JID/LID como fallback.

El diálogo para votar preselecciona el voto propio conocido. En encuestas de voto único presenta
cada opción como un `wx.RadioButton` independiente. Sólo cuando `max-selections` es mayor que uno
presenta `wx.CheckBox` independientes y valida el límite. La respuesta se envía sólo como `<vote>`
y la interfaz considera confirmado el cambio cuando vuelve el correspondiente `<poll-update>`.
Esto evita mostrar como aceptado un voto que WhatsApp no pudo cifrar o enviar.

## Cambios de código

Partir de la misma fuente y dependencias que generaron la imagen v15 fijada por digest. Aplicar los
cambios como un parche reproducible en la construcción de una imagen nueva; no editar archivos
dentro de un contenedor que ya está en ejecución.

1. En `slidge_whatsapp/event.go`, al detectar `PollCreationMessage`, conservar el límite de
   selección. La estructura Python actual ya transporta `Message.Body`, así que puede contener la
   representación decimal del límite sólo para el adaptador:

   ```go
   message.Kind = MessagePoll
   message.Body = strconv.Itoa(int(p.GetSelectableOptionsCount()))
   message.Poll = Poll{Title: p.GetName()}
   ```

   No interpretes el cero sin conocer la variante: V3 usa uno como fallback y V1 usa el total de
   opciones. Esto requiere
   importar `strconv` y no cambia el texto visible de la encuesta.

2. En `slidge/core/mixins/message_text.py`, ampliar `send_text()` con un parámetro explícito
   `extra_xml: ET.Element | None = None`. Después de crear `msg` y antes de `_send()`, hacer
   `msg.xml.append(extra_xml)` cuando exista. Conservar intactas las banderas de reenviado y el
   resto de su comportamiento.

3. En `slidge_whatsapp/session.py`, declarar el namespace y cambiar `on_wa_msg_poll()` para crear
   la extensión `poll`. Debe conservar el fallback de texto actual y llamar a:

   ```python
   actor.send_text(
       body=fallback_body,
       legacy_msg_id=message.ID,
       when=self.__get_timestamp(message),
       carbon=message.Actor.IsMe,
       extra_xml=poll_xml,
   )
   ```

   Copiar `message.Actor.JID`, `message.Actor.LID` y `message.Actor.IsMe` a los atributos del
   contrato. No usar el JID XMPP del contacto como sustituto de estas identidades de WhatsApp.

4. En `slidge/core/dispatcher/message/message.py`, justo después de resolver `recipient` y antes
   de leer cuerpo, adjuntos o respuestas, detectar `vote` con ese namespace. Validar que `id` y
   `creator` no estén vacíos, que las opciones sean texto no vacío, no repetido y que el total sea
   razonable. Llamar a `await recipient.on_poll_vote(...)` y sólo si termina correctamente emitir
   al remitente el `poll-update` propio de confirmación, confirmar la stanza directa con el
   mecanismo de acuse que ya usa Slidge y salir sin pasar por `__dispatch_msg()`.

5. En `slidge_whatsapp/mixins.py`, añadir `RecipientMixin.on_poll_vote()`. Debe crear una
   `whatsapp.Message` con `Kind=whatsapp.MessagePoll`, el `ID` de la encuesta, el `Chat` de
   `get_wa_chat()`, el `Actor` original y un `Poll` cuyas opciones sean las seleccionadas. Luego
   llama al `SendMessage()` ya expuesto por el binding Go. Rechazar grupos sin `creator-lid` y
   chats directos sin `creator`.

6. En `slidge_whatsapp/session.go`, añadir el caso `MessagePoll` a `Session.SendMessage`. No crear
   una encuesta nueva: este caso representa un voto. Construir `types.MessageInfo` con el chat,
   el creador, `IsFromMe`, `IsGroup` e `ID` originales; después usar:

   ```go
   payload, err = client.BuildPollVote(s.ctx, pollInfo, optionNames)
   ```

   y enviarlo por el mismo `client.SendMessage()` normal. `BuildPollVote` recupera el secreto de
   la encuesta desde el almacén de WhatsApp y cifra el voto; nunca mover ese secreto al cliente
   XMPP ni persistirlo en la extensión XML.

7. Reconstruir el binding compartido de Go igual que las imágenes previas: ejecutar `gofmt`, las
   pruebas Go y volver a compilar `generated/_whatsapp...so`. Como el caso reutiliza campos ya
   expuestos de `Message`, `Actor`, `Chat` y `Poll`, no debe ser necesario alterar el API público
   del binding Python.

8. En `event.go`, detectar `PollUpdateMessage`, llamar a `DecryptPollVote`, conservar el ID de la
   creación y convertir cada hash seleccionado a hexadecimal. El mismo camino debe usarse para
   encuestas recuperadas del historial con `ParseWebMessage`.

9. En `session.py`, distinguir creación de actualización por `Message.ReferenceID`. La creación
   emite `<poll>` con fallback visible; la actualización emite `<poll-update>` sin cuerpo de chat.

10. En el cliente, fusionar cada actualización en la encuesta por `(chat, poll_id)` y por identidad
    de votante, recalcular el cuerpo, persistir el `poll_json` actualizado y refrescar sólo ese
    mensaje si la conversación está abierta.

## Pruebas obligatorias antes de desplegar

1. Construir la imagen con la base v15 fijada por digest y una etiqueta candidata local.
2. Ejecutar las pruebas Go del paquete y una prueba de humo Python que compruebe el namespace,
   `on_poll_vote()` y que el binario compartido reconstruido exista.
3. Levantar el contenedor de prueba con una copia aislada de datos; no reutilizar ni borrar la
   sesión de producción para esta prueba.
4. Crear una encuesta desde WhatsApp oficial en un chat directo y en un grupo. Confirmar que el
   cliente actualizado muestra el texto más la acción **Votar en encuesta...**.
5. Elegir una opción desde el cliente y comprobar en WhatsApp oficial que el voto aparece en la
   encuesta correcta. Repetir en un grupo; éste es el caso que prueba `creator-lid`.
6. Reiniciar sólo el contenedor de prueba y comprobar que una encuesta archivada sigue siendo
   votable mientras WhatsApp conserve su secreto local. Si el secreto ya no está disponible, el
   puente debe devolver un error claro y no emitir un mensaje de texto accidental.
7. Cambiar el voto desde WhatsApp oficial y desde el cliente. Confirmar que el total no se duplica,
   que la selección anterior se reemplaza y que quitar el voto reduce el total.
8. Cerrar y abrir el cliente para comprobar que SQLite conserva resultados; después consultar MAM
   para confirmar que las actualizaciones no aparecen como mensajes independientes.

## Despliegue posterior a autorización

Cuando se autorice expresamente, respaldar primero el archivo Compose y registrar el digest de la
imagen activa. Validar el Compose sin imprimir secretos, actualizar sólo la imagen de
`slidge-whatsapp`, recrear únicamente ese servicio y verificar el marcador de arranque de Slidge.
No reiniciar Prosody, no borrar volúmenes y no ejecutar logout. Conservar la referencia exacta a la
imagen anterior para rollback inmediato.

## Alcance de v16

V16 permite leer, votar, cambiar el voto y mostrar los últimos resultados conocidos. Los resultados
son una proyección local de los `PollUpdateMessage` que el puente pudo descifrar y entregar; si el
puente estuvo desconectado y WhatsApp no reenvía un evento, no se inventa el dato faltante. Tampoco
se presentan porcentajes como definitivos mientras existan actualizaciones pendientes.

Las encuestas que el puente ya archivó únicamente como texto, como las recibidas antes de aplicar
este contrato, no pueden volverse votables de forma fiable desde la caché del cliente: carecen de
la identidad del creador y del secreto que se usa para cifrar el voto. Tras el cambio, probar con
una encuesta nueva; una migración histórica sólo sería válida si el puente puede recuperar de
WhatsApp el evento original completo y su secreto asociado.
