# Transcripción de audios de WhatsApp con Deepgram

La imagen `v23` añade transcripción opcional de audios entrantes al puente de WhatsApp.
El archivo multimedia continúa llegando normalmente y, si Deepgram devuelve voz reconocida,
el puente envía un segundo mensaje únicamente hacia XMPP. La transcripción no se reenvía al
contacto de WhatsApp.

La imagen publicada y validada es:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v23
sha256:44f5a5a3ba491bfab28fb280d531d3be535e4628149ec705ff6ee472e1cadb0f
```

Deepgram es opcional. Si `DEEPGRAM_API_KEY` no existe, el puente conserva todas las funciones de
`v22`, no envía audio a Deepgram y los comandos informan que el servicio no está configurado.

Para reconstruir la imagen desde la raíz del repositorio:

```bash
docker build \
  -f tools/Dockerfile.bridge-transcription-v23 \
  -t ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v23 \
  tools
```

## Credenciales

La clave normal de transcripción y la clave administrativa se mantienen separadas:

```env
DEEPGRAM_API_KEY=<clave para Speech-to-Text>
```

```env
DEEPGRAM_OWNER_API_KEY=<clave con permiso para consultar balances>
```

La clave Owner solo se usa cuando se ejecuta `/stats`. Para instalaciones compartidas se
recomienda crear una clave con los permisos mínimos necesarios para consultar el saldo. Ninguna
clave debe incluirse en la imagen, el repositorio o los logs.

Los ejemplos `tools/deepgram.env.example` y `tools/deepgram-owner.env.example` se copian a archivos
distintos fuera del repositorio, se completan allí y se protegen con permisos `600`.

## Configuración

Todas las opciones son configurables mediante variables de entorno:

```env
DEEPGRAM_TRANSCRIPTION_ENABLED=true
DEEPGRAM_MODEL=nova-3
DEEPGRAM_LANGUAGE=es-419
DEEPGRAM_PROJECT_ID=
DEEPGRAM_MAX_AUDIO_BYTES=26214400
DEEPGRAM_MAX_DURATION_SECONDS=900
DEEPGRAM_TRANSCRIPTION_TIMEOUT_SECONDS=120
DEEPGRAM_STATUS_TIMEOUT_SECONDS=15
DEEPGRAM_RETRY_ATTEMPTS=3
DEEPGRAM_DEDUP_RETENTION_DAYS=30
DEEPGRAM_SKIP_MIME_TYPES=audio/mpeg
DEEPGRAM_ALLOWED_MIME_TYPES=
DEEPGRAM_ALLOWED_JIDS=
DEEPGRAM_COMMAND_JIDS=
DEEPGRAM_STATE_PATH=/var/lib/slidge/deepgram-transcription.sqlite3
```

Compose puede cargar ambos archivos de forma opcional:

```yaml
services:
  slidge-whatsapp:
    image: ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v23@sha256:44f5a5a3ba491bfab28fb280d531d3be535e4628149ec705ff6ee472e1cadb0f
    env_file:
      - path: /ruta/privada/deepgram.env
        required: false
      - path: /ruta/privada/deepgram-owner.env
        required: false
```

El directorio que contiene esos archivos debe ser privado y los archivos deben tener permisos
`600`. El valor real de ninguna clave se copia al contexto de build.

`DEEPGRAM_SKIP_MIME_TYPES=audio/mpeg` evita enviar canciones MP3. Si se deja vacío, se aceptan
todos los adjuntos cuyo MIME comienza con `audio/`. `DEEPGRAM_ALLOWED_MIME_TYPES` permite aplicar
una lista positiva más estricta.

`DEEPGRAM_ALLOWED_JIDS` limita qué cuentas registradas reciben transcripciones.
`DEEPGRAM_COMMAND_JIDS` limita quién puede ejecutar los comandos administrativos. Si se dejan
vacías, no se aplica una lista de acceso para conservar compatibilidad con instalaciones previas.

## Comandos

Los comandos se pueden escribir dentro de cualquier chat. El puente los intercepta antes de
WhatsApp y responde solamente por XMPP:

```text
/transcribe on
/transcribe off
/status
/stats
```

`/transcribe of` se acepta como alias de `/transcribe off`. El estado se guarda por cuenta XMPP
y sobrevive a reinicios del contenedor.

`/status` valida la configuración sin enviar audio ni mostrar secretos. `/stats` consulta el saldo
actual del proyecto y lo presenta redondeado a dos decimales.

## Controles operativos

- Solo se procesan audios entrantes en vivo; se omiten historial y mensajes propios.
- Un identificador persistente evita cobrar dos veces por una reentrega del mismo audio.
- Los errores HTTP 429 y 5xx se reintentan con espera exponencial.
- `ffprobe` obtiene la duración mediante entrada estándar, sin dejar archivos temporales.
- Los audios que superan los límites configurados se conservan en el chat, pero no se envían a
  Deepgram.
- Una respuesta válida sin voz reconocida no genera un mensaje de transcripción.

El formato visible incluye duración y tiempo de la petición a Deepgram:

```text
Transcripción: "Texto reconocido". Audio: 1 min 2 s. Transcrito en 4.8 s.
```
