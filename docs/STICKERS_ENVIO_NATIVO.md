# Envío nativo de stickers: cliente corregido, puente pendiente

La opción **Enviar sticker** prepara ahora WebP de 512 × 512 antes de subirlo.
La auditoría del 4 de octubre de 2026 encontró además un fallo de caché en el
puente v28: repetir el mismo sticker puede enviar dos enlaces en vez del sticker.
La corrección del cliente no resuelve por sí sola ese segundo fallo.
No se modificó ni reinició el servidor.

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
  Esto no garantiza que WhatsApp reciba un `AccessibilityLabel`: falta propagación
  en el puente. No se generan descripciones mediante servicios externos.

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
escrituras en producción. No se verificó todavía envío extremo a extremo.

## Corrección del puente propuesta, no aplicada

1. Recuperar el sticker ya cacheado por el SHA-256 calculado antes de insertar,
   en vez de tratar la existencia del contenido como fallo y enviar texto.
2. Probar primer envío y repetición del mismo WebP, en directo y grupo, incluyendo
   respuestas. Un fallo real no debe presentarse como envío exitoso de sticker.
3. Para etiquetas accesibles nativas, transportar `<desc>` hasta
   `StickerMessage.AccessibilityLabel`, separándolo de caption y URL.
   Esto es trabajo adicional al fallo de caché; no se ha implementado.

No usar hashes XEP-0300 (`sha-256`, base64) directamente como CID del almacén
(`sha256`, hexadecimal): requieren normalización en el dispatcher. No se añadió
un hash no estándar al cliente para esquivar el fallo del servidor.

Antes de aplicar: autorización específica, respaldo de archivos efectivos,
pruebas aisladas, conservación del mount de re-login y reinicio sólo del puente
si se autoriza. El gestor de stickers queda fuera de este cambio.

## Referencias

- [Requisitos oficiales de WhatsApp](https://github.com/WhatsApp/stickers/blob/main/Android/README.md): formato, tamaños, animaciones y texto accesible.
- [XEP-0449](https://xmpp.org/extensions/xep-0449.html): marcador sticker, SFS y descripción textual.
- [Pillow WebP](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#webp): codificación y animaciones.

Pruebas: `conda run -n XMPP python -m unittest tests.test_outgoing_stickers
tests.test_whatsapp_message_features tests.test_http_upload` (una sola línea).
Rollback local: retirar `outgoing_stickers.py` y los cambios relacionados de
cliente, UI, dependencia y tests; no revertir el trabajo previo de re-login.
