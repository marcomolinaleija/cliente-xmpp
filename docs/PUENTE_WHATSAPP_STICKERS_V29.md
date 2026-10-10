# Stickers y paquetes: puente v29

**Actualización:** el VPS y manifest estable ahora usan [v30](PUENTE_WHATSAPP_STICKERS_V30.md).
La galería ya permite importar desde el adjunto o su título separado. v29 sigue
disponible; esta página documenta su promoción original, no el estado actual.

**Promoción original (4 de octubre de 2026):** v29 publicada en GHCR y promovida en la única
instancia del VPS. En esa promoción, compose principal y manifest estable apuntaron a v29 por digest
`sha256:8db815f9803ec3a980fa8810b22d385b10489a2ddece63005b0354b521518d05`.
La imagen es descargable sin credenciales. Se preservaron los montajes de datos
y re-login. Backup: `/opt/xmpp/backups/sticker-delivery-v29-20261004T231537Z`;
`rollback.sh` sustituye sólo el puente por la imagen anterior.

Validación: 663 tests de cliente OK, Ruff y diff-check OK, pruebas Go y race OK,
cuatro smokes de imagen con el usuario `slidge` OK. Smoke adicional sin red,
filesystem read-only y tmpfs OK. Fixture recibido por Go y convertido por la
imagen importa con su descripción en la biblioteca real de CAN. El resultado
en WhatsApp oficial con descripción fue confirmado por el usuario en grupo.
Un paquete real reenviado ya aparece en CAN; falta corregir y probar la acción
de importación en el cliente. El envío de paquetes creados por CAN como paquetes
nativos de WhatsApp no forma parte del comportamiento de v29.

## Qué corrige

- Un sticker repetido recupera su entrada BoB por SHA-256. La existencia en caché
  ya no se interpreta como fallo ni se convierte en un mensaje con enlaces.
- SFS/SIMS transporta el texto alternativo por mensaje hasta el campo protobuf
  `StickerMessage.AccessibilityLabel`. No se usa la URL, el cuerpo ni la cita
  como descripción, ni se modifica la descripción compartida en caché.
- `StickerPackMessage` entra por la misma ruta de adjuntos para recepción viva
  e historial. Whatsmeow descarga y autentica el paquete en su dominio de cifrado
  propio; el puente lo convierte a `.canstickers` para importar desde CAN.
- Los fallos reales se mantienen visibles: error XMPP para envío rechazado y
  aviso de paquete no disponible para recepción fallida. No se anuncian enlaces
  de texto como stickers enviados correctamente.

## Formato, límites y privacidad

El sobre interno del puente es un ZIP con manifiesto `whatsapp-sticker-pack`,
versión 1. No es un formato público nuevo: se transforma antes de enviar por
XMPP al formato CAN v1 existente. El cliente no necesita interpretarlo.

Se conservan nombre, autor y descripción de cada sticker; si WhatsApp no aporta
texto accesible, se conservan los emojis como identificación. Los favoritos son
locales. La importación no sobrescribe descripciones existentes en la biblioteca.

Límites: 200 stickers, 120 MiB de paquete/descompresión, 5 MiB por recurso interno,
nombres únicos, sin rutas de escape, enlaces simbólicos ni ZIP cifrados. El
manifiesto referencia exactamente los archivos autenticados del paquete nativo.
Las operaciones no extraen rutas arbitrarias del paquete exterior.

Los WebP animados compatibles permanecen íntegros. Los WAS/Lottie se renderizan
con rlottie a WebP animado de 512×512, sin reducirlos silenciosamente a un frame.
Se limita duración, cantidad de fotogramas, capas y recursos; no se aceptan
recursos externos ni rutas de escape. Una animación incompatible falla de forma
visible, sin importar un paquete incompleto. La conversión es serial y corre
fuera del bucle XMPP. No interviene RayoAI ni se envía contenido a una IA.

## Construcción y validación

`tools/Dockerfile.bridge-sticker-delivery-v29` fija la base v28 por digest.
El parche valida los cuatro archivos antes de escribir y falla ante fuente
inesperada o una aplicación parcial. Una segunda aplicación es inocua.
Se recompila la biblioteca gopy: cambiar sólo `event.go` no cambia el ejecutable.

La construcción ejecuta pruebas Go y race, serialización protobuf de etiquetas,
recepción con downloader ficticio, paquetes inválidos, respuesta citada, smokes
del binding Python real, caché/errores/descripciones SFS/SIMS, normalización CAN
y animación Lottie. También conserva los smokes de stickers entrantes y de
no-auto-join. Las pruebas no usan cuentas, conversaciones ni archivos reales.

Prueba local de interoperabilidad:

```powershell
conda run -n XMPP python -m unittest -q -b tests.test_bridge_sticker_pack
```

Comprobaciones humanas complementarias:

1. Enviar desde la galería un sticker con descripción dos veces al mismo chat.
2. Verificar en la aplicación oficial que ambos son stickers, no enlaces, y
   comprobar con TalkBack/VoiceOver el texto accesible.
3. Repetir con una respuesta citada y en grupo.
4. Compartir desde WhatsApp un paquete nativo hacia la cuenta de CAN; importar
   desde el menú del mensaje y comprobar etiquetas y animaciones.

Los mensajes de paquetes descartados previamente no quedaron almacenados por el
puente. Esta corrección no los reconstruye: es necesario reenviarlos o recibirlos
de nuevo mediante historial que WhatsApp realmente suministre.

## Staging y rollback

No ejecutar dos instancias sobre la misma sesión WhatsApp ni copiar la base de
producción para iniciar una segunda conexión. El staging inicial es una imagen
aislada, sin red ni montajes productivos, con pruebas de runtime sintéticas.
La prueba real requiere sustitución controlada del único servicio del puente,
con copia de la configuración anterior y rollback inmediato si falla.

Mantener los montajes de estado, adjuntos y el parche independiente de re-login.
No cambiar Prosody, nginx ni la vinculación de usuarios. Promover a GHCR con
nueva etiqueta sólo después de validación; actualizar el manifest de bridge con
el digest comprobado, no el manifest del artefacto WSL inmutable.

La publicación de imagen y la publicación Git del manifest son operaciones
distintas. No afirmar que el actualizador remoto ofrece v29 sólo porque el
archivo local fue editado.

## Fuentes del contrato fijado

- [Protobuf de whatsmeow 5f04eac6dbbb](https://github.com/tulir/whatsmeow/blob/5f04eac6dbbb/proto/waE2E/WAWebProtobufsE2E.proto): campos de paquetes y etiquetas.
- [Descarga y dominio MediaStickerPack](https://github.com/tulir/whatsmeow/blob/5f04eac6dbbb/download.go): descarga autenticada; `DownloadAny` no contempla paquetes.
- [XEP-0449](https://xmpp.org/extensions/xep-0449.html): envío de stickers XMPP.
