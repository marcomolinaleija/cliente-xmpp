# Actualizar el puente personalizado de WhatsApp

## Estado actual: modificaciones del puente completadas

Desde el 9 de agosto de 2026, las modificaciones del puente están construidas, publicadas y
activas en `marco-vps`. La imagen vigente es:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v14
sha256:3efeae0eb471bf131fc6af388569ecbd052c14012f6fb963e043a2d1b0760f8f
```

La imagen parte de `v11`, conserva todos los cambios anteriores e incluye:

- Las extensiones anteriores de visualización única y grabación de audio.
- El parche de Slidge core para menciones nativas XEP-0372.
- `rlottie-python==1.3.8` para convertir stickers Lottie a WebP.
- La corrección de nombre y MIME de los adjuntos, al activar las variables documentadas abajo.
- Reenvíos nativos bidireccionales para texto, imagen, audio, video y documentos mediante
  `urn:marco-ml:whatsapp:forwarded:0`.
- Propagación de `events.MarkChatAsRead` desde WhatsApp oficial hacia XEP-0333 en chats
  individuales y XEP-0490 en grupos.
- Persistencia correcta de los adjuntos servidos mediante `NO_UPLOAD_PATH`; el puente ya no
  elimina el archivo que el cliente debe descargar.
- Conservación de la presencia cacheada al actualizar metadatos de contactos, para que un
  `last_seen` válido no sea sustituido por un estado sintético `online` sin fecha.
- Conservación de la última hora conocida cuando WhatsApp envía después una presencia incompleta
  sin timestamp.
- Sincronización automática del roster XMPP después de conectar cada cuenta.
- Fusión condicional de contactos mexicanos duplicados `+521`/`+52`, conservando `+52` como JID
  visible únicamente cuando ambas variantes existen.
- Enrutamiento de texto, archivos, respuestas, reacciones y estados hacia el alias mexicano
  legado `+521` cuando existe el par duplicado, sin volver a exponerlo en el roster XMPP.
- Uso exclusivo de eventos reales de presencia para actualizar `last_seen`; los recibos de
  lectura, estados de escritura y mensajes enviados desde WhatsApp oficial conservan su función
  sin fabricar una última conexión con la hora actual.
- Conservación de los nombres guardados de WhatsApp durante eventos e historial de contactos.
- Entrega directa de las notas PTT entrantes como OGG/Opus, sin la recodificación AAC/M4A que
  añadía pérdida y artefactos audibles en pausas.
- Ciclo de vida seguro para la renovación periódica de presencias: el timer ya no puede usar un
  cliente limpiado concurrentemente ni sobrevivir al teardown de su sesión.

El colaborador **no necesita volver a aplicar los parches ni reconstruir la imagen del puente**.
Si trabaja en otra instalación, debe configurar esa etiqueta y seguir la guía independiente
`docs/PUENTE_WHATSAPP_OTROS_SERVIDORES.md` para conceder los privilegios y validar el despliegue
antes de recrear el servicio:

```bash
cd /opt/xmpp
docker compose pull slidge-whatsapp
docker compose up -d --no-deps --force-recreate slidge-whatsapp
```

En `marco-vps` estos pasos ya se realizaron. Después de confirmar `Successfully authenticated`
y `Login success`, puede concentrarse en modificar y reconstruir `cliente-xmpp`. El código del
cliente no fue modificado durante este despliegue del puente.

### v14: ciclo de vida de renovación de presencias

El 9 de agosto de 2026 se construyó, publicó y desplegó `v14` en `marco-vps`. El contenedor activo
resuelve al digest `sha256:3efeae0eb471bf131fc6af388569ecbd052c14012f6fb963e043a2d1b0760f8f`.
El respaldo previo está en
`/opt/xmpp/backups/presence-lifecycle-v14-20260809-141226/`.

`v13` puede terminar con un panic periódico en `Session.SubscribeToPresences()` cuando el timer de
12 horas con jitter coincide con la limpieza de la sesión. La corrección de `v14` hace que el
renovador capture su propio cliente y contexto, añade cancelación con espera idempotente antes de
limpiar la sesión y usa instantáneas sincronizadas del cliente tanto en la suscripción como en el
dispatcher de eventos. No elimina la renovación de presencias.

La implementación reproducible está en:

- `tools/patch_slidge_whatsapp_presence_lifecycle.py`.
- `tools/bridge_presence_lifecycle_test.go`.
- `tools/Dockerfile.bridge-presence-lifecycle-v14`.
- `tools/smoke_bridge_presence_lifecycle_runtime.py`.

El build ejecuta `go test .`, `go test -race .`, recompila el binding nativo y ejecuta el smoke
runtime. Además se volvieron a ejecutar los trece smoke tests heredados de `v13`. La investigación,
las invariantes de la corrección y el procedimiento de publicación están en
`docs/PUENTE_WHATSAPP_CICLO_PRESENCIAS.md`.

La implementación, el despliegue y la lista de validación de lecturas desde WhatsApp oficial se
documentan en
`docs/PUENTE_WHATSAPP_SINCRONIZACION_LEIDOS.md`. La etiqueta anterior
`read-sync-20260714`/`v4` se conserva para auditoría, pero no debe usarse: esa imagen elimina
los adjuntos entrantes inmediatamente después de anunciar su URL. `puente-completo-20260713`/`v3`
se conserva como rollback anterior sin sincronización de lecturas.

La corrección evita pérdidas nuevas, pero no reconstruye archivos que `v4` ya eliminó. Un mensaje
afectado conservará su URL histórica en el cliente y seguirá devolviendo HTTP 404 hasta que
WhatsApp vuelva a entregar el adjunto; para comprobar la reparación debe recibirse o reenviarse
una nota de voz nueva. No se debe borrar la caché SQLite para intentar recuperarlo.

## Menciones nativas de WhatsApp

El cliente envía referencias XEP-0372 con el JID real de cada integrante del grupo. La imagen
vigente ya contiene el siguiente parche reproducible:

```bash
python tools/patch_slidge_whatsapp_mentions.py RUTA_A_LA_FUENTE_DE_SLIDGE
```

El parche adapta el dispatcher de Slidge para leer las referencias y conservar la identidad del
contacto hasta `slidge-whatsapp`, que escribe `ContextInfo.MentionedJID`. Se conserva en el
repositorio para auditoría y futuras imágenes; no debe reaplicarse manualmente a la imagen activa.

## Construcción reproducible de la imagen completa

La imagen se construyó desde el commit estable `ced2442` de `slidge-whatsapp`, conservando los
cambios anteriores de audio y visualización única. Los parches reproducibles son:

```bash
python tools/patch_bridge_forwarding.py \
  RUTA_A_SLIDGE RUTA_A_SLIDGE_WHATSAPP
python tools/patch_slidge_whatsapp_mentions.py RUTA_A_SLIDGE
python tools/patch_slidge_whatsapp_read_sync.py RUTA_A_SLIDGE_WHATSAPP
python tools/patch_slidge_whatsapp_presence_cache.py RUTA_A_SLIDGE_WHATSAPP
python tools/patch_slidge_whatsapp_presence_last_seen.py RUTA_A_SLIDGE_WHATSAPP
python tools/patch_slidge_whatsapp_roster_sync.py RUTA_A_SITE_PACKAGES
python tools/patch_slidge_whatsapp_presence_sources.py RUTA_A_SITE_PACKAGES
python tools/patch_slidge_whatsapp_message_presence.py RUTA_A_SITE_PACKAGES
python tools/patch_slidge_whatsapp_contact_names.py RUTA_A_SITE_PACKAGES
python tools/patch_slidge_whatsapp_incoming_ptt.py RUTA_A_SITE_PACKAGES
```

`tools/Dockerfile.bridge-completo.patch` documenta los pasos añadidos al Dockerfile de
`slidge-whatsapp`: aplicar los parches antes y después de instalar dependencias y fijar
`rlottie-python==1.3.8`. `tools/Dockerfile.bridge-read-sync.patch` agrega el parche de lecturas y
ejecuta las pruebas Go durante la construcción. Se partió del checkout exacto `88b2f91`; el commit
de fuente finalmente construido en la VPS fue `25431c4`.

La etiqueta `v6` añade únicamente la corrección de presencia sobre el digest validado de
`audio-fix-20260714`, mediante `tools/Dockerfile.bridge-presence-v6`. Así conserva sin cambios
las extensiones anteriores y modifica sólo `slidge_whatsapp/contact.py`.

La etiqueta `v7` parte de `v6` y añade la conservación de `last_seen` mediante
`tools/Dockerfile.bridge-presence-v7`; no modifica ninguna otra función del puente.

La etiqueta `v8` parte de `v7` y añade el parche de roster mediante
`tools/Dockerfile.bridge-roster-v8`. La VPS establece además
`SLIDGE_WHATSAPP_ALWAYS_SYNC_ROSTER=true` para que `GetContacts(refresh=True)` se ejecute al iniciar
cada sesión. `SyncContacts` publica y retira entradas mediante XEP-0356; no se deben reescribir los
archivos `roster/*.dat` de Prosody.

La etiqueta `v9` parte de `v8` y aplica
`tools/patch_slidge_whatsapp_presence_sources.py` mediante
`tools/Dockerfile.bridge-presence-sources-v9`. El parche elimina únicamente las dos llamadas que
usaban `datetime.now()` como `last_seen` al recibir un estado de escritura o un recibo de lectura.
Los métodos `composing`, `paused` y `displayed` permanecen intactos.

La etiqueta `v10` parte de `v9` y aplica
`tools/patch_slidge_whatsapp_message_presence.py` mediante
`tools/Dockerfile.bridge-message-presence-v10`. Elimina la tercera llamada sintética, ejecutada en
`on_wa_message()` al recibir un mensaje enviado desde otra sesión de WhatsApp. La única ruta
autorizada para actualizar la última conexión queda así en `Contact.update_presence()`, con el
timestamp recibido de WhatsApp.

La etiqueta `v11` parte de `v10`, conserva los nombres guardados de WhatsApp al recibir eventos
de contactos y recompila el binding Go mediante `tools/Dockerfile.bridge-contact-names.candidate`.

La etiqueta `v12` parte de `v11` y aplica
`tools/patch_slidge_whatsapp_incoming_ptt.py` mediante `tools/Dockerfile.bridge-audio-v12`.
Elimina únicamente la conversión entrante Opus a AAC en `getMessageAttachments`; las conversiones
de salida hacia WhatsApp permanecen intactas. La construcción recompila el binding nativo y falla
si el binario conserva la ruta `failed to convert incoming attachment`.

La etiqueta `v13` parte del digest publicado de `v12` y aplica
`tools/patch_slidge_whatsapp_mexico_outbound.py` mediante
`tools/Dockerfile.bridge-mexico-outbound-v13`. El parche mantiene el mapa `+521` → `+52` para la
identidad canónica visible y añade el mapa inverso sólo para operaciones salientes. Esto permite
que WhatsMeow resuelva el LID y emita el token de privacidad usando el JID legado que conserva en
su base. `tools/smoke_bridge_mexico_outbound_runtime.py` verifica ambas direcciones y que
`Contact` use el alias saliente sin cambiar su `legacy_id` visible.

La etiqueta `v14` parte del digest publicado de `v13` y aplica
`tools/patch_slidge_whatsapp_presence_lifecycle.py` mediante
`tools/Dockerfile.bridge-presence-lifecycle-v14`. La prueba Go fuerza en paralelo una renovación y
la limpieza del puntero del cliente; el Dockerfile la ejecuta también con el detector de carreras.
El smoke runtime comprueba que el código instalado y el binding reconstruido contienen el nuevo
ciclo de vida. La publicación y el despliegue controlado se documentan en la guía específica.

Las notas PTT originales pueden contener paquetes Opus DTX de 120 ms. Para reproducir sus
transiciones de silencio sin los microcortes del decodificador Opus nativo de FFmpeg,
`cliente-xmpp` prioriza `libopus` mediante la opción libmpv `ad=libopus`. MPV conserva sus
decodificadores normales como fallback para AAC/M4A y los demás formatos. No vuelvas a convertir
estos PTT en el puente para compensar una diferencia del decodificador local.

En el servicio `slidge-whatsapp`, activa además estas variables sin cambiar el comando ni los
volúmenes existentes:

```yaml
environment:
  SLIDGE_CONVERT_STICKERS: "true"
  SLIDGE_FIX_FILENAME_SUFFIX_MIME_TYPE: "true"
```

Antes de publicar, ejecuta dentro de la imagen los smoke tests
`tools/smoke_bridge_mentions_runtime.py`, `tools/smoke_bridge_stickers_runtime.py` y
`tools/smoke_bridge_forwarding_runtime.py`, además de
`tools/smoke_bridge_read_sync_runtime.py` y
`tools/smoke_bridge_attachment_persistence_runtime.py` y
`tools/smoke_bridge_presence_runtime.py` y
`tools/smoke_bridge_presence_last_seen_runtime.py` y
`tools/smoke_bridge_roster_sync_runtime.py` y
`tools/smoke_bridge_presence_sources_runtime.py` y
`tools/smoke_bridge_message_presence_runtime.py` y
`tools/smoke_bridge_contact_names_runtime.py` y
`tools/smoke_bridge_incoming_ptt_runtime.py` y
`tools/smoke_bridge_mexico_outbound_runtime.py` y
`tools/smoke_bridge_presence_lifecycle_runtime.py`. La prueba de stickers debe producir un
WebP válido; comprobar sólo `--help` no demuestra que el motor Lottie esté instalado. La prueba de
persistencia ejecuta el flujo posterior a `send_files` con `NO_UPLOAD_PATH` activo y falla si el
archivo servido desaparece. La escritura de
`ContextInfo.IsForwarded` se cubre con `tools/bridge_forwarding_session_test.go`; las cinco reglas
de `MarkChatAsRead` se cubren con `tools/bridge_read_sync_event_test.go` y `go test ./...`.

Esta guía instala la imagen personalizada del bridge que usa `cliente-xmpp`.
Incluye las extensiones de visualización única y estados de grabación de audio.

## Antes de empezar

- Haz una copia de `/opt/xmpp/compose.yml`.
- No ejecutes `docker compose down -v` ni borres `/opt/xmpp/slidge` o
  `/opt/xmpp/slidge-attachments`: ahí viven la sesión vinculada de WhatsApp y
  los adjuntos persistentes.
- La imagen es privada. Usa un token personal de GitHub con permiso
  `read:packages`; no uses ni compartas la contraseña de XMPP.

## 1. Iniciar sesión en GHCR

```bash
echo 'TOKEN_DE_GITHUB' | docker login ghcr.io -u TU_USUARIO --password-stdin
```

El usuario debe tener acceso de lectura al paquete de GitHub asociado al
repositorio privado `marcomolinaleija/cliente-xmpp`.

## 2. Respaldar la configuración

```bash
cd /opt/xmpp
cp -p compose.yml compose.yml.before-cliente-xmpp-bridge
```

## 3. Cambiar únicamente la imagen del bridge

En el servicio `slidge-whatsapp` de `compose.yml`, usa la imagen vigente:

```yaml
image: ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v14
```

El servicio debe incluir:

```yaml
environment:
  SLIDGE_WHATSAPP_ALWAYS_SYNC_ROSTER: "true"
```

Para instalaciones que ya tengan duplicados mexicanos, detén sólo `slidge-whatsapp`, respalda
`slidge.sqlite`, ejecuta primero la simulación y después la migración explícita:

```bash
docker run --rm \
  -v /opt/xmpp/slidge:/var/lib/slidge \
  -v RUTA_REPO/tools/migrate_slidge_mexico_aliases.py:/tmp/migrate.py:ro \
  --entrypoint python ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v14 \
  /tmp/migrate.py /var/lib/slidge/slidge.sqlite
docker run --rm \
  -v /opt/xmpp/slidge:/var/lib/slidge \
  -v RUTA_REPO/tools/migrate_slidge_mexico_aliases.py:/tmp/migrate.py:ro \
  --entrypoint python ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v14 \
  /tmp/migrate.py --apply /var/lib/slidge/slidge.sqlite
```

No cambies el `command:`, los volúmenes, la red ni las opciones de Prosody.

## 4. Validar y aplicar sin tocar otros servicios

```bash
docker compose config -q
docker compose pull slidge-whatsapp
docker compose up -d --no-deps slidge-whatsapp
```

`--no-deps` es importante: reinicia únicamente el bridge, no Prosody ni los
demás contenedores de la VPS.

## 5. Confirmar que recuperó la sesión

```bash
docker inspect slidge-whatsapp --format 'running={{.State.Running}} restarts={{.RestartCount}} image={{.Config.Image}}'
docker logs --since 5m --tail 80 slidge-whatsapp
```

El resultado esperado incluye `Successfully authenticated` y `Login success`.
Si solicita QR, detente: no borres datos ni vincules otra cuenta sin confirmar
qué sesión se pretende usar.

## 6. Verificación funcional

Desde el cliente actualizado, comprueba:

1. Una nota marcada como `Audio de una sola escucha` llega como visualización
   única en WhatsApp.
2. Al grabar una nota, la otra cuenta ve `grabando audio`.
3. Cuando la otra cuenta graba, el cliente anuncia/muestra `contacto grabando
   audio`.
4. Un mensaje entrante en chat abierto y con la ventana activa usa
   `message.mp3`; en los demás casos usa el sonido normal.
5. Una nota de voz entrante nueva devuelve HTTP 200, aparece bajo
   `/opt/xmpp/slidge-attachments`, se descarga a `%USERPROFILE%\.cliente-xmpp\downloads` y se
   reproduce desde la ruta local.
6. Esa nota nueva se conserva como OGG/Opus (`ffprobe` informa `codec_name=opus`) y no aparece
   como un AAC dentro de un contenedor M4A.

## Rollback

Si el bridge no inicia o no se autentica, restaura el respaldo y recrea solo
ese servicio:

```bash
cd /opt/xmpp
cp -p compose.yml.before-cliente-xmpp-bridge compose.yml
docker compose config -q
docker compose up -d --no-deps slidge-whatsapp
```

No borres volúmenes ni la carpeta `/opt/xmpp/slidge` durante el rollback.
