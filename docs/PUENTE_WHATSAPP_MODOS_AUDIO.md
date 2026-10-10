# Modos de audio adjunto

## Resultado previsto

| Origen en CAN | WhatsApp | CAN |
| --- | --- | --- |
| Adjuntar o pegar un MP3 (extensión sin distinguir mayúsculas) | Audio normal, `PTT=false` | Reproductor habitual |
| Adjuntar o pegar otro formato de audio reconocido: AAC, FLAC, M4A, OGA, OGG, Opus, WAV, WebA | Documento con nombre y MIME de audio | Reproductor habitual |
| Grabar con el micrófono | Nota de voz OGG/Opus, opcionalmente de reproducción única | Reproductor habitual |

Los adjuntos conservan los bytes originales: no pasan por ffmpeg ni por la
conversión multimedia del puente. La consulta de duración usa un worker. Imágenes,
videos y stickers mantienen su comportamiento. No se modifica el historial, SQLite
ni el reproductor; `media_kind=audio`, `audio_url`, MIME y ruta local se conservan
aunque el transporte WhatsApp sea un documento. El receptor también puede
identificar el audio por su MIME original.

El cambio se limita a **nuevos adjuntos**. Reenviar mensajes ya existentes conserva
la ruta anterior; no se infiere retrospectivamente si eran documentos o notas de voz.
Tampoco se puede garantizar qué mensajes transcribirá un bot externo como Zapia.

## Compatibilidad y fallos visibles

CAN consulta la capacidad `urn:can:audio-mode:0` del componente WhatsApp antes de
subir un adjunto de audio. Si falta o la consulta falla, no convierte ni envía la
nota de voz como alternativa. Muestra el error del envío; las grabaciones del
micrófono no requieren esta capacidad. XMPP normal utiliza metadatos SFS/SIMS sin
el hilo privado del puente.

La guarda propia de esta integración permite **hasta 64 MiB por adjunto de audio**;
no es una declaración del límite de WhatsApp. El servidor de subida o WhatsApp
pueden imponer límites menores o rechazar un archivo inválido. No hay reintento
automático cambiando el formato. En el puente la descarga se hace por bloques de
256 KiB, comprobando el tamaño declarado y el acumulado antes de entregar datos al
binding. Una extensión MP3 no valida que sus bytes sean un MP3 real.

## Contrato cliente/puente

- `thread=urn:can:audio-mode:0:audio`: MP3 normal.
- `thread=urn:can:audio-mode:0:document`: otro audio como documento.
- MIME HTTP y metadatos XMPP conservan el tipo real, no `application/octet-stream`.
- El adaptador Python valida intención/MIME y construye una envoltura **interna**
  `application/x-can-audio-mode;modo;MIME-original` en `Attachment.MIME`.
- Go valida de nuevo y atiende esa envoltura **antes** de `convertAttachment`.
  Usa `MediaAudio`/`AudioMessage` sin PTT o `MediaDocument`/`DocumentMessage`.
  WhatsApp recibe el MIME original; la envoltura no se publica como tipo del archivo.
- No se añaden campos a los bindings ni se cambian las reglas existentes de cita,
  reenvío, grabación o reproducción única. Se retira la URL fallback de la leyenda
  de los documentos nuevos; una leyenda real no se descarta.

## Estado y verificación

**v31 publicada en GHCR y promovida al manifiesto estable, tras confirmación
humana del usuario.** Se construyó sobre la imagen v30
efectivamente activa, cuyo digest coincide con el fijado por la receta
`tools/Dockerfile.bridge-audio-modes-v31`. El parche valida los contratos de esa
base y rechaza una aplicación parcial o un origen distinto antes de escribir.
No debe ejecutarse directamente sobre un servicio vivo: hay que reconstruir el
módulo Go y superar las verificaciones de la imagen completa.

La activación autorizada del 6 de octubre de 2026 (7 de octubre, 00:17 UTC)
sustituyó únicamente `slidge-whatsapp`, usando la misma cuenta, sesión y los tres
montajes existentes. No se creó otra instancia ni se pidió vinculación nueva.
La candidata probada fue `cliente-xmpp-bridge:staging-audio-v31-20261007000757z`, ID
`sha256:34be5031b8aed32cdd563268c5aed6b39c35c4f3f2f21343d608417584bf9f84`.
Se publicó **esa misma imagen, sin reconstruirla**, como
`ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v31`, digest de registro
`sha256:46346cd8cd13e69bd9d4808027366fd92ebde23157b4ec1ebb90106d9a6d4cd5`.
La descarga anónima y la identidad del contenido se verificaron. El manifiesto
`tools/wsl-appliance/bridge-update-manifest.json` fija versión 31 y ese digest;
el manifiesto del appliance WSL inmutable no se modifica.

La promoción del 7 de octubre de 2026 UTC fija `v31@sha256:46346cd8...` en
`/opt/xmpp/compose.yml`. El contenedor ya no depende del override temporal de
staging. Sólo se recreó `slidge-whatsapp`, con los mismos datos, variables,
comando, montajes y parche independiente de re-login. La guarda de 60 segundos
confirmó ejecución estable, cero reinicios y ningún error fatal de arranque.

Antes de activarla se respaldó consistentemente el estado con el puente detenido.
El respaldo consistente original permanece en
`/opt/xmpp/backups/audio-modes-v31-20261007000757Z/`. Tras cambiar el Compose
permanente, el rollback vigente es
`/opt/xmpp/backups/audio-modes-v31-promotion-20261007T004037Z/rollback.sh`:
restaura el Compose anterior y la imagen exacta de v30, conservando los datos
actuales, sin restaurar automáticamente una base antigua que pudiera perder
mensajes nuevos. Rechaza deriva posterior del Compose o del parche independiente.
El rollback anterior de staging tiene una guarda del Compose antiguo y no debe
usarse sobre la configuración promovida sin revisión.

La primera promoción ejecutó el rollback porque una comparación ordenada de
`Config.Env` detectó el distinto orden producido por Compose. Se comprobó que
no cambió ningún nombre ni valor: la guarda corregida compara el entorno como
mapa, sin relajar las comprobaciones del comando, montajes o Compose resuelto.
La segunda promoción superó todas las guardas. No se restauró la base de datos.

Comprobaciones locales de Python:

```powershell
conda run -n XMPP python -m unittest discover -s tests -p test_audio_attachment_modes.py -q -b
conda run -n XMPP python -m unittest discover -s tests -p test_bridge_audio_modes.py -q -b
conda run -n XMPP python -m ruff check .
git diff --check
```

La receta ejecutó tests Go de bytes originales, tipo del payload, PTT,
metadatos, fallos y legado, además de `go test -race`. Reconstruye el módulo con
los exports existentes y ejecutó un smoke test del adaptador/binding real sin una
cuenta WhatsApp, junto con los smoke tests anteriores de stickers y sincronización.
Los fixtures locales del parche no sustituyen estas pruebas de imagen.

Las pruebas Go, `go test -race`, compilación del módulo y los seis smoke tests de
imagen **pasaron en la VPS antes de la activación**. También pasaron las 771
pruebas Python del cliente, Ruff y `git diff --check`. La validación contra la base
real detectó una diferencia en la expresión de `Caption` respecto al fixture
inicial: el parche y la regresión se ajustaron al contrato v30, conservando el
tratamiento de stickers. Estos checks no sustituyen la prueba en WhatsApp oficial.

Para probar, reiniciar CAN desde el código actualizado, no desde un ejecutable
anterior. No se construyó un nuevo binario Windows.

El usuario confirmó que todo funciona antes de autorizar la publicación estable.
Esa confirmación no constituye un informe desglosado de cada formato, dispositivo
o escenario NVDA. Para futuras regresiones, comprobar MP3, WAV/M4A/OGG como
documentos, reproducción local (pausa, velocidad, avance y secuencia automática),
micrófono, citas de grupo, teclado y NVDA. No usar conversaciones reales en tests
destructivos. Preparar la versión del cliente no compila ni publica su ejecutable.
