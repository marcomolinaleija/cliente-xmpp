# Envío nativo de stickers: cliente y puente v29

**Actualización:** después de autorización explícita, se implementaron caché,
etiquetas y paquetes en el [puente v29](PUENTE_WHATSAPP_STICKERS_V29.md).
Se publicó en GHCR y se promovió con rollback. El VPS y manifest estable ahora
usan [v30](PUENTE_WHATSAPP_STICKERS_V30.md), que añade paquetes salientes nativos.
El usuario confirmó envío nativo de un sticker con descripción en
WhatsApp oficial en un grupo. Los
hallazgos siguientes documentan la auditoría inicial de v28.

La opción **Enviar sticker** prepara ahora WebP de 512 × 512 antes de subirlo.
La auditoría del 4 de octubre de 2026 encontró además un fallo de caché en el
puente v28: repetir el mismo sticker puede enviar dos enlaces en vez del sticker.
La corrección del cliente no resuelve por sí sola ese segundo fallo.
En esa auditoría inicial no se modificó ni reinició el servidor.

## Comportamiento del cliente

- PNG/JPEG/WebP estáticos se ajustan sin recortar, con relleno transparente y
  límite de 100 KB. No se elimina automáticamente el fondo de la imagen.
- Se intenta WebP sin pérdida primero; sólo si supera el límite se reduce calidad.
  El original nunca se modifica. Un WebP estático compatible se reutiliza intacto.
- WebP animado compatible se conserva íntegro, incluyendo metadatos. Se valida
  tamaño de 512 × 512, hasta 500 KB, fotogramas de al menos 8 ms y hasta 10 segundos.
  Animaciones incompatibles se rechazan; no se envía silenciosamente el primer frame.
- La preparación corre fuera de los hilos wx/asyncio. Las copias se guardan en
  `downloads` mediante `.part`; tras fallo de subida sólo se elimina la copia nueva.
- El envío normal de fotografías no se convierte en sticker.
- Reenvíos conservan descripciones existentes en UI y `<desc>` SFS/SIMS.
  En v28 falta propagación a `AccessibilityLabel`; v29 la incluye.
  El resultado en la aplicación oficial fue confirmado por el usuario. La preparación
  del envío no genera descripciones mediante servicios externos.

## Evidencia del puente efectivo

Consulta SSH de sólo lectura: contenedor `slidge-whatsapp`, imagen v28. Conserva
el soporte nativo XEP-0449 y construcción de `waE2E.StickerMessage`, además del
mount independiente de la corrección de re-login. No hay que reemplazarlo todo.

| Archivo bajo `/venv/lib/python3.13/site-packages` | Hallazgo |
| --- | --- |
| `slidge_whatsapp/event.go::uploadStickerAttachment` | Exige WebP; logs recientes mostraron `native WhatsApp stickers must be WebP`. CAN enviaba PNG. |
| `slidge/core/dispatcher/message/message.py::__dispatch_nonbob_sticker` | Sin CID, descarga y llama `set_sticker`; si recibe `None`, envía URL + fallback como texto. CAN usa la URL como fallback: se duplica. |
| `slidge/db/store.py::set_sticker` | Si el contenido ya existe, retorna `None` en vez del sticker existente. |
| `slidge_whatsapp/mixins.py::on_sticker` | Crea adjunto nativo, pero no transporta la descripción SFS como etiqueta accesible WhatsApp. |

El fallback duplicado se reprodujo ejecutando la función efectiva con respuestas
y almacén ficticios: `duplicated_url=True`, sin red, destinatarios reales ni
escrituras en producción. El usuario confirmó después que el envío corregido
funciona. En esa primera revisión no se verificaron la repetición de contenido cacheado ni
la etiqueta accesible en las aplicaciones oficiales.

Una nueva prueba del usuario desde la galería volvió a aparecer como enlace.
La auditoría de sólo lectura confirmó WebP válido y descripción coincidente en
biblioteca, mensaje local y archivo de Prosody. El BoB del mismo contenido tenía
archivo anterior al envío: no es necesario haber enviado antes ese sticker desde
la galería para caer en el fallo de caché. Se reejecutó la función efectiva con
fakes y se confirmó la rama de fallback a texto. El estado XMPP `delivered` no
garantiza que WhatsApp haya recibido `StickerMessage`.

## Corrección identificada en v28 y aplicada en v29

1. Recuperar el sticker ya cacheado por el SHA-256 calculado antes de insertar,
   en vez de tratar la existencia del contenido como fallo y enviar texto.
2. Probar primer envío y repetición del mismo WebP, en directo y grupo, incluyendo
   respuestas. Un fallo real no debe presentarse como envío exitoso de sticker.
3. Para etiquetas accesibles nativas, transportar `<desc>` hasta
   `StickerMessage.AccessibilityLabel`, separándolo de caption y URL.
   Esto es trabajo adicional al fallo de caché. En v28,
   el parser de adjuntos descarta `<desc>`, `on_sticker` deja `Caption`
   vacío y `buildStickerMessage` no establece la etiqueta. La descripción debe
   viajar por mensaje (sin sobrescribir metadatos compartidos de la caché);
   el builder Go requiere compilar y desplegar el puente corregido.

No usar hashes XEP-0300 (`sha-256`, base64) directamente como CID del almacén
(`sha256`, hexadecimal): requieren normalización en el dispatcher. No se añadió
un hash no estándar al cliente para esquivar el fallo del servidor.

La imagen se construyó sobre el digest efectivo v28, recompiló gopy y pasó
pruebas aisladas; conserva el mount de re-login. Un puente local/remoto que siga
en v28 conserva sus fallos. El smoke usa SQLite y BoB reales para probar cuatro
envíos del mismo contenido con descripciones distintas, sin fallback a enlaces.
Conviene complementar la confirmación humana de envío en grupo con repetición,
respuesta citada e importación del paquete recibido.

## Referencias

- [Requisitos oficiales de WhatsApp](https://github.com/WhatsApp/stickers/blob/main/Android/README.md): formato, tamaños, animaciones y texto accesible.
- [XEP-0449](https://xmpp.org/extensions/xep-0449.html): marcador sticker, SFS y descripción textual.
- [Pillow WebP](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#webp): codificación y animaciones.

Pruebas: `conda run -n XMPP python -m unittest tests.test_outgoing_stickers
tests.test_whatsapp_message_features tests.test_http_upload` (una sola línea).
Rollback local: retirar `outgoing_stickers.py` y los cambios relacionados de
cliente, UI, dependencia y tests; no revertir el trabajo previo de re-login.
