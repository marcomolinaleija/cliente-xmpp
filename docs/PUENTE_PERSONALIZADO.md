# Actualizar el puente personalizado de WhatsApp

## Menciones nativas de WhatsApp

El cliente envía referencias XEP-0372 con el JID real de cada integrante del grupo. Para que se
conviertan en menciones nativas de WhatsApp, la fuente de Slidge usada en la imagen debe recibir
este parche antes de construirla:

```bash
python tools/patch_slidge_whatsapp_mentions.py RUTA_A_LA_FUENTE_DE_SLIDGE
```

El parche adapta el dispatcher de Slidge para leer las referencias y conservar la identidad del
contacto hasta `slidge-whatsapp`, que escribe `ContextInfo.MentionedJID`. Después hay que
reconstruir y publicar la imagen `cliente-xmpp-bridge:v1` con el mismo procedimiento habitual.
No basta con actualizar sólo el cliente: WhatsApp requiere esa metadata en el mensaje saliente.

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

En el servicio `slidge-whatsapp` de `compose.yml`, sustituye solo la línea
`image:` por:

```yaml
image: ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v1
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
