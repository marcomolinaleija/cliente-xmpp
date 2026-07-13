# Puente WhatsApp: stickers, menciones y reenvios

Guia unica para Marco. Este documento describe los cambios que pertenecen al
bridge `Slidge + slidge-whatsapp`; no son cambios que se puedan resolver solo
desde `cliente-xmpp`.

## Estado de implementación

Los tres cambios de esta guía quedaron implementados, probados, publicados y
activos en `marco-vps` el 13 de julio de 2026. La imagen completa es:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:puente-completo-20260713
sha256:82540ad56a6b4b293252b1dc864689ea39baac37a092a6a3c4597a4153b586b0
```

También existe el alias `v3`. El servicio recuperó la sesión con
`Successfully authenticated` y `Login success`, sin reinicios. El cliente no
se modificó durante el despliegue del puente. El checkout actual de
`cliente-xmpp` ya consume `urn:marco-ml:whatsapp:forwarded:0`, reconoce
XEP-0449 para stickers y conserva ambas banderas en SQLite. Para usar la
integración completa sólo falta reconstruir o reiniciar el cliente con este
checkout.

## Contrato implementado en cliente-xmpp

- Los stickers entrantes se reconocen por
  `<sticker xmlns="urn:xmpp:stickers:0"/>`, se muestran como `Sticker`, se
  descargan en segundo plano y no exponen nombres hash a NVDA.
- El botón `Enviar sticker...` adjunta XEP-0449 al archivo para activar
  `on_sticker` en Slidge.
- Los mensajes reenviados conservan `is_forwarded` en vivo, inbox, MAM y
  SQLite. La lista, el lector detallado, los previews y NVDA anuncian
  `Reenviado`.
- `Reenviar...` permite elegir un chat individual o grupo y emite la bandera
  privada tanto para texto como para foto, audio, video, documento o sticker.
  Sólo se reutiliza el contenido y la URL del adjunto; no se copia la identidad
  del remitente ni la cita original.
- Las menciones siguen emitiendo XEP-0372 con JID y rangos Unicode desde el
  autocompletado de grupos. No se sustituyen por texto `@nick`.

Las pruebas locales de contrato viven en
`tests/test_whatsapp_message_features.py` y `tests/test_mentions.py`. La prueba
final en las aplicaciones oficiales de WhatsApp sigue siendo necesaria después
de reconstruir el cliente.

## Alcance y regla de trabajo

- Trabajar sobre los checkouts exactos de Slidge y `slidge-whatsapp` usados por
  la imagen que se despliega. No editar un contenedor en ejecucion como unica
  copia del cambio.
- Guardar los cambios en un fork o commit propio y reconstruir la imagen desde
  ese commit.
- Antes de reiniciar, respaldar `compose.yml`. No borrar los volumenes ni
  `/opt/xmpp/slidge`: ahi vive la sesion vinculada de WhatsApp.
- Verificar primero las rutas y simbolos con `rg`; las versiones de Slidge
  pueden moverlos.

```bash
cd RUTA_A_LA_FUENTE_DE_SLIDGE
rg -n "parse_mentions\\(body\\)|async def parse_mentions" \
  slidge/core/dispatcher/message/message.py slidge/group/room.py

cd RUTA_A_LA_FUENTE_DE_SLIDGE_WHATSAPP
rg -n "IsForwarded|MentionJIDs|ContextInfo" \
  slidge_whatsapp/event.go slidge_whatsapp/mixins.py slidge_whatsapp/session.go
```

## 1. Stickers Lottie que llegan como `.bin`

### Diagnostico

Un sticker Lottie puede llegar como un ZIP con `animation/animation.json`, pero
con nombre `.bin` y MIME `application/octet-stream`. El cliente lo interpreta
entonces como archivo generico. No hay que renombrar manualmente ni borrar esos
archivos historicos.

### Cambio en configuracion

La ruta recomendada es hacer que el bridge convierta los stickers Lottie nuevos
a WebP animado y corrija la extension segun el MIME:

```ini
convert-stickers=true
fix-filename-suffix-mime-type=true
```

Equivalente por variables de entorno:

```bash
SLIDGE_CONVERT_STICKERS=true
SLIDGE_FIX_FILENAME_SUFFIX_MIME_TYPE=true
```

Para una prueba temporal, si la version instalada expone las opciones:

```bash
slidge-whatsapp --convert-stickers=true --fix-filename-suffix-mime-type=true
```

Confirma antes con `slidge-whatsapp --help | rg -i sticker`; no todas las
versiones publican exactamente las mismas opciones.

### Validacion

1. Enviar un sticker animado nuevo desde WhatsApp.
2. Confirmar que llega con MIME de imagen y extension `.webp`, no `.bin`.
3. Confirmar que `cliente-xmpp` lo clasifica como imagen/sticker y que NVDA no
   anuncia un hash como nombre principal.
4. Repetir con un sticker estatico y otro paquete animado.
5. Conservar los `.bin` existentes como evidencia y para soporte futuro; esta
   configuracion no los transforma retroactivamente.

### Rollback

Si la conversion falla, volver ambos valores a `false`, reiniciar solo el
bridge y conservar cache y base de mensajes.

## 2. Menciones nativas de WhatsApp

### Resultado esperado

Cuando el cliente seleccione a una persona mediante `@` en un grupo, WhatsApp
debe recibir `ContextInfo.MentionedJID`. El texto visible por si solo no basta:
la persona mencionada debe recibir el comportamiento nativo de WhatsApp.

El cliente ya envia una referencia XEP-0372 con el JID y el rango Unicode:

```xml
<message type="groupchat" to="#grupo@whatsapp.xmpp.rayoscompany.com">
  <body>Hola Jessy Herrera</body>
  <reference xmlns="urn:xmpp:reference:0"
             type="mention"
             uri="xmpp:+521234567890@whatsapp.xmpp.rayoscompany.com"
             begin="5" end="18" />
</message>
```

`begin` es inclusivo y `end` exclusivo. El JID, no el nombre visible ni el nick,
es la identidad que se debe conservar.

### Cambio obligatorio en Slidge core

El problema esta antes de `slidge-whatsapp`: Slidge core debe recibir el XML de
la stanza junto con el texto y convertir las referencias explicitas en objetos
`Mention`.

En este repositorio existe un parche reproducible:

```bash
python tools/patch_slidge_whatsapp_mentions.py RUTA_A_LA_FUENTE_DE_SLIDGE
```

El parche realiza estos cambios:

1. En `slidge/core/dispatcher/message/message.py`, pasa `msg.xml` a
   `recipient.parse_mentions(body, msg.xml)`.
2. En `slidge/group/room.py`, lee referencias `urn:xmpp:reference:0` de tipo
   `mention`, valida rango y JID, busca al participante real y construye el
   `Mention` correspondiente.
3. Da prioridad a referencias explicitas validas. Si no existen, conserva la
   deteccion historica por nick para clientes XMPP antiguos.

La parte final ya existe en `slidge-whatsapp`: `mixins.py` llena
`Message.MentionJIDs` y `session.go` lo copia a
`ExtendedTextMessage.ContextInfo.MentionedJID`.

### Validacion

```bash
rg -n "parse_mentions\\(body, msg\\.xml\\)" \
  slidge/core/dispatcher/message/message.py
rg -n "urn:xmpp:reference:0|explicit_mentions" slidge/group/room.py
rg -n "MentionJIDs|ContextInfo\\.MentionedJID" \
  slidge_whatsapp/mixins.py slidge_whatsapp/session.go
```

Probar con dos cuentas de WhatsApp, una cuenta del bridge y un grupo:

1. Mencionar una persona mediante el autocompletado del cliente.
2. Repetir con dos personas, tildes y nombres personalizados distintos del nick
   de WhatsApp.
3. Confirmar en la app oficial que es una mencion nativa y no texto plano.
4. Confirmar que un mensaje sin referencias sigue funcionando.

Para diagnostico temporal, registrar solo el conteo de `xmpp_msg.mentions` y de
`Message.MentionJIDs`; no registrar cuerpos ni datos de sesion.

## 3. Reenvios nativos de WhatsApp

### Estado actual del bridge

WhatsApp expresa la etiqueta mediante `ContextInfo.isForwarded`; tambien tiene
`forwardingScore` para el caso de "reenviado muchas veces". El `event.go` actual
ya declara `Message.IsForwarded` y lee `info.GetIsForwarded()`, pero hay tres
perdidas de metadata:

1. La asignacion ocurre despues de interpretar `participant`; si ese JID no
   existe, el retorno temprano puede perder la etiqueta.
2. `session.py` convierte la etiqueta de texto en el prefijo literal
   `Forwarded message`; el cliente no recibe un dato estructurado.
3. Los adjuntos no reciben esa etiqueta y `session.go` no escribe
   `ContextInfo.IsForwarded` al enviar.

Por tanto, copiar un texto desde el cliente hoy solo envia un mensaje nuevo sin
la etiqueta nativa de WhatsApp.

### Contrato XMPP privado recomendado

No reutilizar `urn:xmpp:forward:0` para esta funcion. Ese namespace es XEP-0297
y el bridge ya lo usa como envoltorio de MAM; mezclar ambos significados crearia
ambiguedad entre historial y reenvio del usuario.

Definir una extension privada, registrada en Slidge y documentada junto al
bridge:

```xml
<message type="chat" to="contacto@whatsapp.xmpp.rayoscompany.com">
  <body>Contenido reenviado</body>
  <forwarded xmlns="urn:marco-ml:whatsapp:forwarded:0" />
</message>
```

El mismo elemento debe poder acompanar la stanza que transporta una imagen,
audio, video o documento. Es una bandera; no debe incluir el JID ni el XML
original del remitente, porque reenviar no debe filtrar identidad ajena.

### Cambios aplicados al bridge

#### WhatsApp -> XMPP

1. En `slidge_whatsapp/event.go`, copiar `info.GetIsForwarded()` a
   `message.IsForwarded` inmediatamente despues de comprobar que `info` no es
   nulo, antes de cualquier retorno por `participant` invalido.
2. Conservar opcionalmente `forwardingScore` como entero de solo lectura. No
   inventar ni incrementar el score en el bridge.
3. Anadir una extension stanza de Slixmpp para
   `urn:marco-ml:whatsapp:forwarded:0` y una ruta en Slidge core para adjuntarla
   antes de enviar el mensaje XMPP.
4. Pasar esa bandera desde `session.py` tanto a `actor.send_text()` como a
   `actor.send_files()`. La API actual de Slidge crea y envia la stanza dentro
   de esos metodos, asi que se necesita un hook pequeno en el creador de
   mensajes para agregar la extension antes de `_send()`.
5. Eliminar el prefijo textual `Forwarded message` una vez que el cliente ya
   consuma la extension. Mientras se despliegan ambas partes puede mantenerse
   solo como compatibilidad temporal, nunca como fuente de verdad.

#### XMPP -> WhatsApp

1. En el dispatcher de Slidge core, leer la extension privada desde `msg.xml`
   y exponer una bandera `is_forwarded` al adaptador. Debe llegar a
   `mixins.py` para texto y archivos.
2. En `_on_text()` y `_on_file()` de `slidge_whatsapp/mixins.py`, crear el
   `whatsapp.Message` con `IsForwarded=True` cuando la stanza tenga la
   extension.
3. En `slidge_whatsapp/session.go`, crear una funcion comun que escriba
   `ContextInfo.IsForwarded=true` en el payload final. Debe cubrir
   `ExtendedTextMessage` y los payloads de imagen, audio, video y documento
   producidos despues de subir un adjunto.
4. Un texto reenviado no puede quedarse como `Conversation` plano: para llevar
   `ContextInfo` debe enviarse como `ExtendedTextMessage` con `Text` y
   `ContextInfo`.

La implementación reproducible vive en `tools/patch_bridge_forwarding.py` y
se valida con `tools/smoke_bridge_forwarding_runtime.py` y
`tools/bridge_forwarding_session_test.go`.

No marcar correcciones, reacciones ni retractaciones como reenvios. Para la
primera entrega basta `isForwarded=true`; `forwardingScore` se conserva solo si
vino de WhatsApp y se debe validar en las apps oficiales antes de mostrar
"reenviado muchas veces".

### Pruebas de aceptacion

Hacer las pruebas con el bridge reconstruido y dos cuentas de WhatsApp:

1. Recibir un texto reenviado en chat individual y grupo: el cliente debe
   recibir la extension, no solo un prefijo de texto.
2. Recibir una foto, audio y documento reenviados: los tres deben conservar la
   bandera, incluido al llegar por MAM.
3. Desde el cliente, reenviar texto, foto, audio y documento a otro chat.
   WhatsApp debe mostrar su etiqueta nativa de reenviado.
4. Confirmar que un mensaje normal no recibe la etiqueta y que una respuesta,
   reaccion o edicion conserva su comportamiento actual.
5. Probar con historia, carbon y mensaje vivo para evitar que una ruta pierda
   la metadata.

## Despliegue y rollback comun

1. Construir la imagen privada desde el commit que contiene los cambios.
2. En la VPS, cambiar solo la imagen del servicio `slidge-whatsapp`.
3. Validar la configuracion y recrear unicamente ese servicio:

```bash
cd /opt/xmpp
docker compose config -q
docker compose pull slidge-whatsapp
docker compose up -d --no-deps slidge-whatsapp
docker logs --since 5m --tail 80 slidge-whatsapp
```

No usar `docker compose down -v`. Si el bridge no inicia o pierde autenticacion,
restaurar el `compose.yml` respaldado y recrear solo `slidge-whatsapp`; no borrar
la sesion ni los adjuntos.

## Referencias

- [Configuracion de slidge-whatsapp](https://slidge.im/docs/slidge-whatsapp/main/admin/config.html)
- [XEP-0372: References](https://xmpp.org/extensions/xep-0372.html)
- [XEP-0297: Stanza Forwarding](https://xmpp.org/extensions/xep-0297.html)
- [Fuente de Slidge](https://codeberg.org/slidge/slidge)
- [Fuente de slidge-whatsapp](https://codeberg.org/slidge/slidge-whatsapp)
