# Menciones nativas de WhatsApp: cambios requeridos en el puente

## Resultado esperado

Al seleccionar una persona con `@` desde `cliente-xmpp` y enviar un mensaje a un grupo, las
apps oficiales de WhatsApp deben recibir una mención nativa: el nombre se muestra como mención
en la conversación y la persona mencionada recibe la notificación correspondiente. No debe
llegar solamente el texto del nombre o del apodo.

## Diagnóstico confirmado

El cliente ya envía una referencia explícita XEP-0372 por cada mención seleccionada:

```xml
<message type="groupchat" to="#grupo@whatsapp.xmpp.rayoscompany.com">
  <body>Hola Jessy Herrera</body>
  <reference xmlns="urn:xmpp:reference:0"
             type="mention"
             uri="xmpp:+521234567890@whatsapp.xmpp.rayoscompany.com"
             begin="5"
             end="18" />
</message>
```

`begin` es inclusivo y `end` exclusivo; ambos cuentan posiciones Unicode del `body`.
La referencia contiene el JID real del participante, por lo que no depende del nombre
personalizado que Ángel tenga guardado localmente.

El problema está en el puente actual:

1. Slidge core recibe el mensaje de grupo, pero llama a `parse_mentions(body)` sin pasar el XML
   de la stanza. Por ello solo intenta deducir menciones buscando texto de nicks.
2. Esa deducción no puede saber con certeza a qué contacto corresponde un nombre, ni aprovecha
   el JID explícito enviado por el cliente.
3. Como Slidge no llena `xmpp_msg.mentions`, `slidge-whatsapp` recibe una lista vacía y no manda
   la metadata nativa a WhatsApp. El texto llega, pero es texto literal.

La parte final de `slidge-whatsapp` **ya existe** en la versión auditada: transforma
`xmpp_msg.mentions` en `Message.MentionJIDs`, y su código Go copia esa lista a
`ExtendedTextMessage.ContextInfo.MentionedJID`. No hace falta añadir ni modificar Prosody,
ni inventar otro formato de WhatsApp. Hay que alimentar correctamente esa lista desde Slidge.

## Cambio obligatorio en Slidge core

El repositorio del cliente incluye el parche reproducible:

```text
tools/patch_slidge_whatsapp_mentions.py
```

El script modifica la fuente de **Slidge core**, no la del cliente ni la configuración de
Prosody:

- `slidge/core/dispatcher/message/message.py`
  - Cambia la llamada de `recipient.parse_mentions(body)` a
    `recipient.parse_mentions(body, msg.xml)` para conservar las referencias XEP-0372.
- `slidge/group/room.py`
  - Amplía `Room.parse_mentions()` para leer cada
    `<reference type="mention" uri="xmpp:..." begin="..." end="..."/>`.
  - Valida rango y JID, busca el participante de ese grupo y construye el objeto `Mention`
    asociado a su contacto.
  - Si hay referencias explícitas válidas, estas tienen prioridad. Si no las hay, conserva el
    comportamiento histórico de detección por nick para clientes XMPP antiguos.

La prioridad de referencias explícitas es necesaria: dos personas pueden tener nicks iguales o
cambiar su apodo de WhatsApp, mientras que el JID del participante identifica al destinatario
correcto.

## Implementación para Marco

### 1. Trabajar sobre la misma versión que se construye en la imagen

En el checkout de Slidge que utiliza el Dockerfile del bridge, verifica primero que los puntos de
anclaje existan. El script falla de forma segura si la versión cambió y no encuentra exactamente
el código esperado.

```bash
cd RUTA_A_LA_FUENTE_DE_SLIDGE
rg -n "parse_mentions\(body\)" slidge/core/dispatcher/message/message.py
rg -n "async def parse_mentions" slidge/group/room.py
```

### 2. Aplicar el parche antes de construir la imagen

Desde este repositorio, o copiando el script al contexto de construcción:

```bash
python tools/patch_slidge_whatsapp_mentions.py RUTA_A_LA_FUENTE_DE_SLIDGE
```

El script crea archivos `.bak` por seguridad. Para una construcción limpia y repetible se puede
usar `--no-backup` una vez que el cambio ya esté guardado como commit en el fork de Slidge.

Lo recomendable es **llevar el resultado a un fork/commit propio del bridge** y hacer que el
Dockerfile construya desde ese commit. No depender de un parche manual dentro de un contenedor
en ejecución: se perdería al reconstruir o actualizar la imagen.

### 3. Comprobar que el cambio quedó incluido

Antes de crear la imagen, estas comprobaciones deben producir resultados:

```bash
rg -n "parse_mentions\(body, msg\.xml\)" \
  slidge/core/dispatcher/message/message.py
rg -n "urn:xmpp:reference:0|explicit_mentions" slidge/group/room.py
```

Además, en la fuente de `slidge-whatsapp` incluida en la imagen debe seguir existiendo la cadena
que ya implementa el extremo WhatsApp:

```bash
rg -n "MentionJIDs" slidge_whatsapp/mixins.py
rg -n "ContextInfo\.MentionedJID" slidge_whatsapp/session.go
```

Si alguna de estas rutas o símbolos cambió por una actualización, hay que adaptar el parche a
esa versión y añadir una prueba; no conviene forzar una sustitución textual.

### 4. Reconstruir y publicar el bridge

Construir la misma imagen privada usada por el servicio `slidge-whatsapp`, por ejemplo:

```bash
docker build -t ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v1 .
docker push ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v1
```

En la VPS, actualizar únicamente el bridge, preservando la sesión y volúmenes existentes:

```bash
cd /opt/xmpp
docker compose pull slidge-whatsapp
docker compose up -d --no-deps slidge-whatsapp
```

No ejecutar `docker compose down -v` ni borrar `/opt/xmpp/slidge`: allí vive la sesión vinculada
de WhatsApp.

## Prueba de aceptación obligatoria

Usar dos cuentas de WhatsApp: la cuenta del bridge y una cuenta participante del grupo.

1. En `cliente-xmpp`, abrir un grupo y seleccionar una persona con el autocompletado `@`.
2. Enviar un texto que incluya una mención, una tilde y más texto, por ejemplo:
   `Hola Jessy Herrera, prueba de mención.`
3. En la app oficial de WhatsApp del destinatario, verificar que se muestra como mención nativa
   y no como texto plano. La persona mencionada debe recibir el comportamiento de notificación
   propio de WhatsApp.
4. Repetir con dos participantes en el mismo mensaje.
5. Repetir con un contacto cuyo nombre personalizado en el cliente sea distinto de su nick de
   WhatsApp. Debe mencionar a la persona correcta igualmente.
6. Confirmar que un mensaje de grupo sin referencias XEP-0372 sigue enviándose sin errores.

Para diagnosticar un fallo, añadir temporalmente un log en el bridge justo antes de enviar a
WhatsApp que muestre solo el conteo de `xmpp_msg.mentions` y `Message.MentionJIDs`, nunca el
cuerpo completo ni datos de sesión. El valor esperado para una mención es `1` en ambos puntos.

## Qué no resuelve este parche

- No convierte un texto escrito manualmente como `@nombre` en una mención inequívoca. Para eso
  el mensaje debe venir del autocompletado del cliente, que adjunta el JID y el rango XEP-0372.
- No requiere cambios en SQLite, roster de Prosody ni en la interfaz de WhatsApp.
- No debe sustituir el nombre visible del mensaje por el nombre personalizado local; WhatsApp
  decide cómo presentar al contacto, mientras que la metadata `MentionedJID` determina a quién
  se menciona realmente.

## Referencias

- [XEP-0372: References](https://xmpp.org/extensions/xep-0372.html): define `reference`,
  `type="mention"`, URI `xmpp:` y los rangos `begin`/`end`.
- [Slidge](https://github.com/slidge-im/slidge): core que recibe y normaliza la stanza XMPP.
- [slidge-whatsapp](https://github.com/slidge-im/slidge-whatsapp): adaptador que ya propaga
  `MentionJIDs` hacia `ContextInfo.MentionedJID` de WhatsApp.
