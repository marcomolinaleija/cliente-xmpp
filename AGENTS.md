# cliente-xmpp — Guía de trabajo

Cliente Windows Python/wxPython para XMPP y el puente de WhatsApp. Estas reglas activas sustituyen la cronología operativa que antes ocupaba este archivo.

## Responsabilidades

- `cliente_xmpp/app/main.py`: arranque; `ui/main_window.py`: coordinación; `ui/conversation_panel.py`: lectura y acciones.
- `cliente_xmpp/xmpp/client.py`: protocolo y `XmppService`; `xmpp/events.py`: eventos tipados; `models/`: datos compartidos; `storage/message_store.py`: SQLite.
- wx corre en su hilo; XMPP en el bucle asyncio del servicio. Cruza mediante los eventos/colas existentes y llamadas thread-safe. No manipules controles desde callbacks de red, Windows o workers.
- Mantén XML/slixmpp fuera de UI, wx fuera de modelos/storage/protocolo, y consultas SQLite dentro de storage. No bloquees handlers con red, MAM, descargas, ffmpeg o agregados.

## Invariantes de mensajes e identidad

- Fusiona MAM, inbox, carbons, vivo y ecos de MUC de forma idempotente por cuenta/chat/identidad; nunca dedupliques sólo por texto o por ser saliente.
- `notify=False` permite persistir y actualizar previews, pero no anunciar ni incrementar no leídos. Historial nunca debe parecer nuevo. Conserva la autoridad del resumen inicial del bridge y los marcadores de lectura hasta poder resolver su mensaje; no borres mensajes posteriores.
- Muestra memoria/caché antes de consultar la red; páginas remotas complementan, no vacían la vista. Conserva el orden inicial que habilita el control de actividad antes de unirse a grupos; no conviertas precargas en cargas ilimitadas.
- Los previews sólo avanzan con mensajes más recientes. Los upserts enriquecen citas y rutas locales; entrega y retracción son monotónicas. Mantén WAL, migraciones defensivas y datos existentes.
- En MUC conserva room JID e identidad del participante; no mezcles historial grupal con chats individuales. Un título técnico no debe degradar un nombre humano.
- Una respuesta grupal conserva `reply@to` con room/recurso MUC, no el JID privado. Conserva borrador si falta identidad remota válida; no conviertas una respuesta fallida en mensaje normal.
- No borres ecos por nombre de bot, `outgoing` o `is_self_group`. La correlación debe ser estructural y acotada.
- Un error sólo puede retirar el optimista propio correspondiente, no mensajes remotos. No reconectes MUC por todo rechazo ni generalices reintentos; conserva límites y motivos específicos.
- Respeta los perfiles local/remoto y sus credenciales independientes. No sobrescribas el remoto al instalar el puente local.

## Accesibilidad, medios y privacidad

- Conserva foco, selección y posición de lectura ante actualizaciones. Valida teclado, Escape, Enter, flechas y NVDA con historiales grandes y texto largo.
- No uses tooltips de cuerpos completos ni autoajuste masivo de columnas. Mantén etiquetas accesibles acotadas y el contenido completo disponible en lectura/copia; toda congelación de lista debe terminar en `Thaw`.
- Respeta mute, mensajes propios y `notify`; no dupliques sonido cuando Windows acepta un toast. Las acciones del toast vuelven al hilo wx.
- Descarga a `.part`, reemplaza al terminar y persiste ruta sólo tras éxito. Audio reproduce el archivo local, no un fallback HTTP. Conserva MIME/URL originales y los adaptadores de stickers/Lottie; un `.bin` o WebP cualquiera no es automáticamente sticker.
- Mantén ocultos los procesos multimedia mediante el helper existente y ejecutores seriales donde preservan orden. No recodifiques con pérdida para ocultar un problema de reproducción.
- Retracción detiene reproducción y elimina sólo la copia local exacta; historial tardío no puede restaurarla. La limpieza de almacenamiento sólo actúa dentro de raíces administradas, no sigue enlaces y conserva confirmaciones/protecciones de archivos en uso.
- Estadísticas son locales; no envíes conversaciones a servicios externos. Conserva el aviso sobre límites de las tendencias emocionales.
- Antes de commit/push revisa datos personales nuevos: teléfonos, nombres, JID, grupos o contenidos reales requieren detenerse y preguntar si anonimizar o incluir expresamente. Una autorización genérica previa no basta. Usa fixtures ficticios; no copies SQLite, credenciales ni logs reales. No reescribas historial publicado sin permiso.

## Validación desde la raíz

`pyproject.toml` declara Python, dependencias y Ruff. El entorno local documentado es `XMPP`; confirma su disponibilidad sin instalar nada automáticamente.

- Suite: `conda run -n XMPP python -m unittest discover -s tests`.
- Lint: `conda run -n XMPP python -m ruff check .`.
- Estructura: `git diff --check`.
- Lanzamiento real, sólo cuando corresponda: `conda run -n XMPP python -m cliente_xmpp.app.main`.
- Ejecuta pruebas focalizadas durante cambios y la suite completa antes de entregar una release, salvo límite explícito del usuario. Las pruebas no sustituyen validación real de conexión, MAM, envío/recepción, multimedia y NVDA. No uses datos del usuario para pruebas destructivas.

## Consulta por tema y entrega

- Para un cambio en notificaciones, medios, deduplicación, sincronización o grupos, consulta sólo la sección correspondiente de [la referencia operativa preservada](docs/agent-context-2026-09-04.md), luego comprueba el código y los tests actuales.
- Para WSL2 consulta `docs/PUENTE_WHATSAPP_WSL2.md`; para lectura `docs/PUENTE_WHATSAPP_SINCRONIZACION_LEIDOS.md`; para llamadas `docs/PUENTE_WHATSAPP_LLAMADAS_V27.md`; para transcripción `docs/PUENTE_WHATSAPP_TRANSCRIPCION_DEEPGRAM.md`.
- Las versiones, digests, servidores, rutas y validaciones de esa referencia son históricos, NO estado de producción confirmado ni órdenes ejecutables. Verifica el estado real antes de una operación autorizada; no reapliques parches ni despliegues por leer el histórico.
- `build_release.ps1` construye artefactos; `publish_release.bat` publica. Lee el script vigente antes de una acción expresamente autorizada y conserva sus barreras de árbol limpio, checksums y releases existentes. Build, commit, push, tag y publicación son autorizaciones separadas.
