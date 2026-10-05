# Paquetes nativos salientes: v30

## Publicación y despliegue

- Imagen pública: `ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v30`.
- Digest: `sha256:88dc92a92e30fc8b57704f0c4d64d392e37d6f917278989f57f5ec3849945adf`.
- El manifest estable apunta a ese digest. Consulta anónima de GHCR y pull
  confirmaron la misma imagen de release.
- Promovido únicamente `slidge-whatsapp` en el VPS: running, cero reinicios,
  tres montajes idénticos y hash del parche independiente de re-login intacto.
  La auditoría inicial no encontró ERROR, FATAL ni Traceback; esto no demuestra
  por sí solo entrega end-to-end ni renderizado del teléfono.
- Respaldo privado y rollback a v29:
  `/opt/xmpp/backups/native-sticker-packs-v30-20261005T002118Z/rollback.sh`.
  Conserva compose y fija la imagen anterior; no borra datos ni recrea otros servicios.
- Checkout limpio de publicación: 671 tests (59,348 s), Ruff y `git diff --check`.
  El árbol local también pasó 674, incluyendo tres tests independientes no publicados.
  Imagen: Go tests y race,
  cinco smokes como usuario slidge, y smoke nativo adicional sin red/read-only.
  Fixture sintético Go → receptor → biblioteca CAN conserva dos assets,
  descripciones y animación.

## Alcance

v29 ya está publicado y recibe paquetes oficiales como CAN v1. v30 añade el
envío explícito de paquetes creados en CAN como `StickerPackMessage`, no como
documentos ZIP ni enlaces. No cambia el formato CAN v1 ni los envíos normales
de documentos, fotografías o stickers individuales.

El nuevo cliente consulta `urn:can:sticker-pack:0` por disco antes de subir.
Si el puente no anuncia soporte, falla visiblemente sin enviar un documento
disfrazado. Clientes anteriores siguen usando sus formatos existentes.

## Contrato y límites

- SFS/SIMS marca `application/x-can-sticker-pack`. El puente reconoce la
  intención por metadata, no por el MIME HTTP (que puede ser octet-stream).
- Descarga limitada a 32 MiB incluso sin Content-Length; validación y preparación
  serial en worker, no en el bucle XMPP. 1–60 stickers, WebP ya compatibles;
  no convierte un paquete incompleto ni aplana animaciones.
- ZIP nativo contiene exactamente WebP y portada, sin manifiesto CAN. Cada
  descriptor protobuf referencia su archivo por nombre y conserva
  `AccessibilityLabel` y `isAnimated`. Origen `USER_CREATED`.
- ZIP cifrado con `MediaStickerPack`; miniatura JPEG 252×252 cifrada con la
  misma clave y dominio HKDF distinto `WhatsApp Sticker Pack Thumbnail Keys`.
  Ruta de subida `thumbnail-sticker-pack`. Se transportan SHA-256, hashes
  cifrados, tamaños, timestamp y rutas devueltas por WhatsApp.
- El clasificador vendorizado añade `mediatype=sticker_pack`, no `text`.
  Respuestas y la marca de reenvío se conservan en `ContextInfo`.
- `.canstickers` exportado conserva 1–200 elementos; compartir nativamente
  aplica límites más conservadores. `.wastickers` sigue siendo para importadores
  móviles, no se renombra ni se anuncia como mensaje nativo.

## Evidencia y comprobación real

Pruebas: CAN v1 a sobre nativo con bytes/animación/descripciones preservados;
portada WebP 96×96 y JPEG 252×252; Go construye protobuf y verifica roundtrip
por el receptor v29. Cifrado de miniatura se autentica y descifra, y se comprueba
separación HKDF. Fallos de pack/subida/miniatura no producen documentos.
Binding gopy compilado recibe MIME nativo aunque HTTP devuelva octet-stream.
Los smokes anteriores de caché, etiquetas, animación y no-auto-join siguen activos.

El paquete real recibido se importó en una biblioteca temporal sin cambiar datos
del usuario. La acción sobre el título separado tiene pruebas de menú contextual,
identidad, retracción y ambigüedad. El renderizado de un paquete **saliente** en
la aplicación oficial todavía necesita confirmación humana: una prueba de
protobuf no demuestra la presentación del teléfono.

Construcción: `tools/Dockerfile.bridge-native-sticker-packs-v30`, base v29 fijada
por digest. Sólo se extiende la copia vendorizada del builder; no se modifica
el caché global de Go ni se actualizan dependencias a otra versión. Se conserva
el mount independiente de re-login y se sustituye sólo el servicio del puente.

## Fuentes primarias

- [Protobuf fijado de whatsmeow](https://github.com/tulir/whatsmeow/blob/5f04eac6dbbb/proto/waE2E/WAWebProtobufsE2E.proto).
- [Upload y metadata criptográfica](https://github.com/tulir/whatsmeow/blob/5f04eac6dbbb/upload.go).
- [Implementación de builder de paquetes](https://github.com/QueenAnya/Bail/blob/master/src/addons/from-messages.ts).
- [Dominios HKDF y rutas del mismo fork](https://github.com/QueenAnya/Bail/blob/master/src/Defaults/index.ts).

El fork es evidencia de implementación, no una garantía oficial de compatibilidad
ni una dependencia añadida al proyecto. La prueba humana sigue siendo necesaria.
