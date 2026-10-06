# Galería de stickers de CAN

**Enviar sticker** abre la biblioteca, no el explorador. **Ver → Galería de
stickers** (`Ctrl+Mayús+S`) permite administrarla incluso sin conexión. La guía
integrada con F1 incluye el recorrido de uso.

## Crear, identificar y agrupar

- Crea desde PNG, JPEG o WebP, o desde **Crear sticker...** en el menú del mensaje
  enfocado. Si el mensaje ya es un sticker, la acción se llama **Guardar sticker...**.
  No aparece para texto, documentos arbitrarios o mensajes retirados.
- CAN copia el resultado a `~/.cliente-xmpp/stickers/files/` y guarda nombres,
  descripciones, favoritos y paquetes en `stickers/library.sqlite3`. No modifica
  la fuente. Cada envío usa otra copia en descargas: borrar el sticker no rompe
  la copia local del mensaje enviado.
- Las fotos se orientan según EXIF y se ajustan sin recorte a WebP 512×512 con
  relleno transparente. No se elimina el fondo. Se intenta codificación sin
  pérdida y se reduce calidad sólo si el límite estático de 100 KB lo exige.
- WebP animado compatible se conserva sin aplanar: hasta 500 KB, fotogramas de
  al menos 8 ms y duración total de hasta 10 segundos. Si sus dimensiones no son
  512×512, se ajustan todos los fotogramas sin recorte, con relleno transparente y
  codificación sin pérdida; se conservan tiempos, repeticiones y metadatos. También
  se intenta recodificar sin pérdida cuando supera 500 KB. Si no cabe en ese límite
  o requiere demasiada memoria, se informa del error sin degradarlo ni modificar
  el original. Lottie requiere aceptar
  una imagen fija representativa; el original animado se conserva.
- Descripción manual, RayoAI o ninguna. Recordar **RayoAI siempre** requiere
  elección explícita y se revierte en **Biblioteca → Preferencias**. RayoAI recibe la imagen y
  puede contactar al proveedor externo configurado; no se envía el chat.
  Si falla la descripción, se conserva el sticker para editarlo manualmente.
- Si el mensaje ya tiene texto alternativo, se guarda directamente con él, sin
  preguntar ni consultar RayoAI, incluso con la descripción automática activada.
- Una descripción manual existente no se sobrescribe al importar el mismo
  contenido. **Describir con RayoAI...** permite revisar el resultado antes de
  reemplazarla. Una revisión tardía no pisa una edición más reciente.
- Un sticker puede estar en varios paquetes. Borrar un paquete sólo elimina
  agrupación; borrar un sticker requiere confirmación y afecta su copia propia.

## Compartir paquetes y exportar: distintos alcances

| Formato | Uso | Límites y metadatos |
| --- | --- | --- |
| `.canstickers` | Exportar/importar con CAN o adjuntar al chat | 1–200 stickers; conserva nombre, autor y descripciones. Los favoritos son personales y no se exportan. |
| `.wastickers` | Entregar a un importador móvil compatible | 3–30 stickers, todos estáticos o todos animados; título, autor y portada. No conserva descripciones. |
| Compartir paquete en el chat | Mensaje nativo WhatsApp con puente v30 | CAN limita este envío a 1–60 stickers y 32 MiB; conserva autor, nombre, animación y etiquetas accesibles. |

Un archivo `.wastickers` no es una API oficial ni una instalación universal en
WhatsApp. Su estructura ZIP sigue una [implementación de formato de un
importador](https://github.com/laggykiller/wastickers_creater): `title.txt`,
`author.txt`, `cover.png` y archivos de stickers. La compatibilidad depende de la
aplicación receptora y debe probarse en el teléfono.

Instalar desde una aplicación móvil de terceros usa integración específica: en Android,
[ContentProvider e intención de incorporación aprobada por el
usuario](https://github.com/WhatsApp/stickers/blob/main/Android/README.md); en
iOS, [la integración de su muestra oficial](https://github.com/WhatsApp/stickers/blob/main/iOS/README.md).
CAN no instala una app móvil ni publica automáticamente paquetes mediante PEP.

**Compartir paquete en el chat** es distinto de exportar: envía un
`StickerPackMessage` mediante el [puente v30](PUENTE_WHATSAPP_STICKERS_V30.md),
con portada dentro del ZIP y miniatura cifrada separada. El formato de biblioteca
CAN v1 no cambia: otros CAN pueden importar el paquete recibido como antes.
El cliente consulta la capacidad del puente antes de subir; si éste es antiguo,
muestra un error y ofrece exportar, sin presentar un documento como paquete nativo.

Los paquetes CAN son ZIP planos con `manifest.json`, formato `can-stickers`,
versión 1, y archivos WebP. Importar valida rutas, duplicados, tipos, tamaños y
contenido antes de confirmar la transacción. No extrae rutas arbitrarias.
Exportar reemplaza atómicamente el destino y no puede sobrescribir la biblioteca
interna. La importación está limitada a 120 MB, 200 stickers y 204 entradas ZIP.

## Lectura accesible y límites del puente

Lista nativa wx paginada de 100 elementos con miniaturas y columnas acotadas.
La pantalla habitual muestra búsqueda, filtro, stickers y vista previa, con
**Crear**, **Acciones**, **Biblioteca**, **Enviar** (sólo en un chat) y **Cerrar**.
**Biblioteca** reúne importación, gestión/exportación/envío de paquetes y
preferencias. Anterior/Siguiente sólo aparecen cuando hay paginación; Ctrl+F
enfoca Buscar y Ctrl+RePág/AvPág cambia de página. Al abrir, el foco queda en la
lista de stickers; Alt+M vuelve a ella y Alt+O enfoca el filtro Mostrar.
Las recargas conservan el foco que hayas elegido. La lista usa dos columnas;
favorito y animación se anuncian con el nombre, no en columnas adicionales.
El filtro conserva el foco al usar flechas, incluso durante la carga. Los cambios
rápidos aplican la última selección; Tab permite pasar a la lista.
Flechas seleccionan, F2 renombra el sticker seleccionado, Espacio lee la descripción
completa, Mayús+F10/tecla de
menú/Acciones abre las mismas operaciones. El texto completo también está en
un control de lectura y copia. Enter envía sólo en el selector abierto desde
un chat; al iniciar el envío, el foco vuelve a la lista de mensajes. Escape cierra.
Las miniaturas de animaciones muestran un fotograma.

Conversión, SQLite, descarga y RayoAI corren en un worker serial, fuera del hilo
wx. Cerrar no cancela una operación ya iniciada: termina en segundo plano y sus
callbacks no actualizan controles destruidos.

La descripción se envía como `<desc>` en SFS/XEP-0449 y se conserva localmente.
En v28, WhatsApp no recibe la etiqueta accesible y un sticker cacheado puede
terminar como enlace. El [puente v29](PUENTE_WHATSAPP_STICKERS_V29.md) corrige
ambas rutas y recibe paquetes nativos como `.canstickers`; se importa desde el
menú del archivo o del título separado **Paquete de stickers: …**. v29 está
publicado en GHCR; el manifest estable y el VPS ahora usan v30. El usuario confirmó sticker con
descripción en un grupo oficial. La importación desde el título exige un único
adjunto vecino del mismo remitente, dirección, chat y timestamp exacto; no fusiona
mensajes ni modifica identidades. Archivos comunes ZIP no activan esta acción.
La auditoría de sólo lectura del envío reportado confirmó que el WebP y su
descripción llegaron a Prosody, pero el contenido ya existía en la caché del
puente. La reproducción aislada de la función efectiva devuelve enlaces en
vez de sticker nativo. Un acuse `delivered` XMPP no prueba el tipo entregado a
WhatsApp. La auditoría inicial no modificó el servidor; posteriormente se
autorizó y promovió v29, después v30, con respaldo y rollback. Los paquetes descartados
por v28 pueden necesitar ser reenviados: no se reconstruyen de un archivo local
que nunca se recibió.

## Comprobación antes de distribuir

Pruebas automatizadas:

```powershell
conda run -n XMPP python -m unittest tests.test_sticker_library tests.test_sticker_gallery tests.test_sticker_gallery_integration tests.test_outgoing_stickers
conda run -n XMPP python -m unittest discover -s tests
conda run -n XMPP python -m ruff check .
git diff --check
```

Las pruebas wx usan controles nativos ocultos, archivos ficticios y respuestas
RayoAI simuladas. No sustituyen esta prueba humana:

1. Desde fuente, abrir la galería con teclado/NVDA; crear una foto no cuadrada.
2. Probar las tres opciones de descripción, recordar/desactivar RayoAI y editar
   el resultado. Comprobar foco, Escape, Enter, flechas y Mayús+F10.
3. Marcar favorito, añadir a dos paquetes, buscar por descripción y cambiar de
   página. Eliminar un paquete sin perder stickers.
4. Enviar a un chat y a un grupo con cita; repetir el mismo sticker y comprobar
   WhatsApp oficial. Distinguir cualquier fallo del puente de la preparación.
5. Exportar/importar un paquete CAN y compartirlo con otro CAN: verificar texto
   alternativo. Probar `.wastickers` con el importador concreto del teléfono.
6. Eliminar el sticker del gestor y confirmar que la foto original y la copia
   enviada siguen disponibles. Probar fallo de RayoAI y cierre durante tarea.
