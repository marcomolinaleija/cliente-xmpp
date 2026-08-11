# Encuestas nativas de WhatsApp: cambio requerido en el puente

## Estado y objetivo

El cliente ya reconoce el contrato XMPP privado `urn:marco-ml:whatsapp:poll:0`, conserva sus
metadatos en SQLite y muestra **Votar en encuesta** junto al historial cuando se selecciona una
encuesta, igual que la acción para ir a un mensaje citado. No debe duplicarse esa acción en el
menú contextual.

La imagen actual del puente reconoce `PollCreationMessage`, pero lo convierte a este texto de
compatibilidad antes de enviarlo por XMPP:

```text
🗳 Título
☐ Opción 1
☐ Opción 2
```

Además, actualmente descarta `PollUpdateMessage`. Por ello el texto llega al cliente, pero no la
identidad criptográfica que WhatsApp exige para emitir un voto. No se debe intentar votar enviando
un mensaje de texto, una reacción ni un hash calculado desde el cliente.

Este cambio mantiene el texto anterior para clientes XMPP que aún no soporten encuestas y añade
metadatos sólo para el cliente actualizado. No toca cuentas, QR, sesiones vinculadas, roster,
Prosody ni las bases de datos de Slidge.

## Contrato XMPP

El puente debe añadir al mensaje XMPP de creación:

```xml
<poll xmlns="urn:marco-ml:whatsapp:poll:0"
      id="ID-WHATSAPP-DE-LA-ENCUESTA"
      title="Pregunta"
      creator="numero@s.whatsapp.net"
      creator-lid="identidad@lid"
      creator-is-me="false"
      max-selections="1">
  <option>Primera opción</option>
  <option>Segunda opción</option>
</poll>
```

Los atributos `id`, `title`, `creator` y al menos una opción son obligatorios. En grupos debe
conservarse también `creator-lid`: WhatsApp usa esa identidad al cifrar votos. `max-selections`
es el valor real de `SelectableOptionsCount`; cuando WhatsApp entrega cero, el puente debe
anunciar el número de opciones como límite práctico para el cliente.

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

## Cambios de código

Partir de la misma fuente y dependencias que generaron la imagen v14 fijada por digest. Aplicar los
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

   Si el límite es cero, el adaptador Python lo sustituye por el número de opciones. Esto requiere
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
   razonable. Llamar a `await recipient.on_poll_vote(...)`, confirmar la stanza directa con el
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

## Pruebas obligatorias antes de desplegar

1. Construir la imagen con la base v14 fijada por digest y una etiqueta nueva e inmutable.
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

## Despliegue posterior a autorización

Cuando se autorice expresamente, respaldar primero el archivo Compose y registrar el digest de la
imagen v14 que está activa. Validar el Compose sin imprimir secretos, actualizar sólo la imagen de
`slidge-whatsapp`, recrear únicamente ese servicio y verificar el marcador de arranque de Slidge.
No reiniciar Prosody, no borrar volúmenes y no ejecutar logout. Conservar la referencia exacta a la
imagen anterior para rollback inmediato.

## Alcance de la primera entrega

Esta entrega permite leer y votar encuestas. Los votos enviados se confirman por el estado de envío
del cliente y por WhatsApp oficial.

## Resultados y cambios de voto

La segunda fase procesa `PollUpdateMessage`. El bridge debe descifrarlo exclusivamente mediante
`client.DecryptPollVote(ctx, evt)`, que usa el secreto que WhatsApp conserva en su propio almacén.
Debe emitir una stanza auxiliar, sin cuerpo visible, con los hashes SHA-256 de las opciones:

```xml
<poll-update xmlns="urn:marco-ml:whatsapp:poll:0"
             id="ID-WHATSAPP-DE-LA-ENCUESTA"
             voter="numero@s.whatsapp.net"
             voter-lid="identidad@lid">
  <option hash="sha256-en-hexadecimal"/>
</poll-update>
```

El cliente ya conoce los textos de las opciones, calcula sus hashes localmente y conserva una
selección vigente por votante. Al llegar un voto nuevo del mismo votante, reemplaza el anterior,
recalcula los totales y ordena las opciones de mayor a menor (manteniendo el orden original en
empates). No se envían textos de opciones, secretos ni resultados falsos desde XMPP hacia
WhatsApp.

Las encuestas que el puente ya archivó únicamente como texto, como las recibidas antes de aplicar
este contrato, no pueden volverse votables de forma fiable desde la caché del cliente: carecen de
la identidad del creador y del secreto que se usa para cifrar el voto. Tras el cambio, probar con
una encuesta nueva; una migración histórica sólo sería válida si el puente puede recuperar de
WhatsApp el evento original completo y su secreto asociado.
