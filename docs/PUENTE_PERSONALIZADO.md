# Actualizar el puente personalizado de WhatsApp

## Estado actual: modificaciones del puente completadas

Desde el 18 de julio de 2026, las modificaciones del puente están construidas, publicadas y
activas en `marco-vps`. La imagen vigente es:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v8
sha256:64811c17a2b12c90d0f0c4fb0e7654d5663031a7d351342c089945b4d9100fe3
```

También está publicada como `roster-sync-20260718`. Esta imagen incluye:

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
`tools/smoke_bridge_roster_sync_runtime.py`. La prueba de stickers debe producir un
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
image: ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v8
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
  --entrypoint python ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v8 \
  /tmp/migrate.py /var/lib/slidge/slidge.sqlite
docker run --rm \
  -v /opt/xmpp/slidge:/var/lib/slidge \
  -v RUTA_REPO/tools/migrate_slidge_mexico_aliases.py:/tmp/migrate.py:ro \
  --entrypoint python ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v8 \
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
