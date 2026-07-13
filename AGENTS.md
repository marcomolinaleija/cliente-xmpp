# AGENTS.md

## Proposito de este archivo

Este archivo da contexto operativo a agentes de IA que trabajen en este repositorio.
Debe leerse antes de editar codigo. Funciona como una guia tecnica del proyecto: explica
que es el cliente, como fluye la informacion, donde vive cada responsabilidad y que reglas
seguir para mantener una arquitectura escalable.

`AGENTS.md` es el nombre estandar que varias herramientas de agentes detectan en la raiz
del repositorio. Mantenerlo en la raiz permite que las instrucciones apliquen a todo el
proyecto. Si en el futuro hay submodulos con reglas distintas, se puede agregar otro
`AGENTS.md` mas cercano a esos archivos.

## Que es este proyecto

`cliente-xmpp` es una aplicacion de escritorio para Windows escrita en Python. Usa wxPython
para la interfaz nativa y slixmpp para conectarse a un servidor XMPP que actua como bridge
de WhatsApp. El objetivo es ofrecer una experiencia propia, accesible y fluida para leer,
responder y gestionar chats expuestos por el bridge.

La aplicacion trabaja con mensajes 1 a 1, historial XMPP, mensajes entrantes en vivo,
respuestas/citas, previews de la lista de chats, contenido multimedia detectado en stanzas
XMPP y una cache local SQLite para acelerar la apertura y evitar depender de la red para
mostrar conversaciones ya conocidas.

## Entorno de desarrollo

El entorno esperado del usuario es Conda:

```powershell
conda activate XMPP
```

Comandos utiles:

```powershell
python -m pip install -e .
cliente-xmpp
python -m cliente_xmpp.app.main
python -m compileall cliente_xmpp
python -m ruff check .
git diff --check
```

No asumas que otro entorno tiene todas las dependencias instaladas. Si necesitas validar el
proyecto, activa primero `XMPP`. No agregues dependencias de produccion sin una razon clara:
este es un cliente desktop pequeno y cada dependencia afecta instalacion, arranque y soporte.

## Estructura del repositorio

```text
cliente_xmpp/
  app/            Punto de entrada de la aplicacion.
  accessibility/  Integracion con lector de pantalla/NVDA.
  assets/         Recursos empaquetados, como audio de notificacion.
  audio/          Reproduccion de audio y sonidos locales.
  config/         Ajustes locales y credenciales.
  models/         Dataclasses compartidas por capas.
  storage/        Persistencia local SQLite de chats y mensajes.
  ui/             Ventanas, paneles wxPython y eventos UI.
  xmpp/           Conexion slixmpp, stanzas, MAM, eventos y envio.
```

Archivos clave:

- `cliente_xmpp/app/main.py`: arranque minimo de wx.
- `cliente_xmpp/ui/main_window.py`: orquestacion entre UI, XMPP, cache local y estado.
- `cliente_xmpp/ui/conversation_panel.py`: renderizado y acciones de la conversacion.
- `cliente_xmpp/ui/chat_list_panel.py`: lista de chats y previews.
- `cliente_xmpp/xmpp/client.py`: cliente XMPP, recepcion/envio, MAM y multimedia.
- `cliente_xmpp/xmpp/events.py`: eventos tipados que cruzan del hilo XMPP a wx.
- `cliente_xmpp/models/chat.py`: modelos `Chat` y `Message`.
- `cliente_xmpp/storage/message_store.py`: SQLite local para chats y mensajes.

## Capas y responsabilidades

### Capa de aplicacion

La capa `app/` solo debe iniciar la aplicacion. No debe contener reglas de negocio,
persistencia ni detalles XMPP. Si el arranque necesita mas pasos, extraelos a funciones
pequenas y manten el punto de entrada legible.

### Capa de modelos

`models/` contiene datos compartidos entre UI, XMPP y storage. Los modelos deben ser simples:
dataclasses, campos explicitos y sin dependencias de wxPython, slixmpp ni SQLite.

Agrega campos a `Message` o `Chat` solo cuando representen datos del dominio usados por mas
de una capa. Evita meter comportamiento pesado en los modelos.

### Capa XMPP

`xmpp/` encapsula slixmpp y los protocolos. Esta capa debe:

- Conectar y desconectar la cuenta.
- Leer roster/chats del bridge.
- Recibir mensajes entrantes y carbons.
- Consultar historial con MAM.
- Parsear respuestas/citas y fallback bodies.
- Detectar multimedia en stanzas XMPP.
- Emitir eventos propios del proyecto, no objetos wx.

La UI no debe manipular stanzas XML directamente. Si una pantalla necesita un dato nuevo
del protocolo, extraelo en `xmpp/client.py`, ponlo en `Message` o en un evento tipado y
consume ese dato desde la UI.

### Capa UI

`ui/` contiene wxPython. Esta capa debe:

- Renderizar listas, formularios, estados y dialogos.
- Mantener la interaccion accesible por teclado.
- Evitar bloquear el hilo principal.
- Recibir eventos XMPP ya normalizados.
- Pedir acciones al servicio XMPP sin conocer detalles de protocolo.

No hagas llamadas de red ni consultas SQLite pesadas directamente dentro de handlers de UI si
pueden bloquear. Para trabajo largo, usa el servicio existente, colas o llamadas diferidas.

### Capa de storage

`storage/` persiste datos locales. SQLite se usa como cache durable para abrir la app rapido
y mostrar chats/mensajes antes de que termine la sincronizacion XMPP.

La base local vive bajo el directorio de aplicacion del usuario, actualmente
`%USERPROFILE%\.cliente-xmpp\messages.sqlite3`.

Reglas de storage:

- Los upserts deben ser idempotentes.
- No dupliques mensajes si llega el mismo item por MAM, carbon o mensaje en vivo.
- No dejes que un mensaje viejo pise el preview de uno mas reciente.
- Conserva datos locales utiles, como ruta descargada de multimedia, aunque un evento nuevo
  traiga menos informacion.
- Mantener migraciones compatibles hacia adelante usando columnas nuevas con defaults.

## Flujo de datos

1. El usuario inicia sesion desde la UI.
2. `MainWindow` crea/configura `XmppService`.
3. El servicio XMPP conecta, carga roster y emite eventos a wx.
4. Al llegar el roster, la UI carga chats cacheados desde SQLite para mostrar algo rapido.
5. En segundo plano se consultan actualizaciones e historiales recientes.
6. Los eventos `MessageReceived` y `MessageHistoryLoaded` se fusionan en memoria.
7. Los mensajes se persisten en SQLite.
8. La lista de chats recalcula orden, preview y hora por recencia.
9. Si el chat esta abierto, la conversacion se refresca sin pisar interacciones activas.

El estado en memoria (`messages_by_chat`, timestamps, sets de carga) existe para que la UI sea
rapida. SQLite existe para durabilidad y arranque rapido. XMPP sigue siendo la fuente remota,
pero nunca debe hacer que la pantalla quede vacia si hay cache valida.

## Contexto operativo vigente

Esta seccion describe el comportamiento real del checkout. Debe actualizarse cuando cambie un
flujo de eventos o una regla de negocio, porque es la referencia mas util para un agente nuevo.

### Hilos y frontera entre XMPP y wx

- wxPython corre en el hilo de UI y `BridgeXmppClient` corre dentro del hilo asyncio propio de
  `XmppService`.
- `BridgeXmppClient` nunca debe tocar controles wx. Emite dataclasses de `xmpp/events.py`.
- `XmppService` entrega esos eventos a wx mediante `wx.PostEvent`; `MainWindow` es el unico
  orquestador que muta listas, conversacion, estado de cache y notificaciones.
- Las acciones desde UI hacia XMPP deben entrar por `XmppService`, que usa
  `call_soon_threadsafe`. No bloquees handlers wx con red, MAM, SQLite, ffmpeg ni descargas.

### Fuentes de mensajes y semantica de eventos

Hay cuatro caminos que pueden representar el mismo mensaje:

1. MAM (`MessageHistoryLoaded`) para historial y precargas.
2. Inbox del bridge (`MessageReceived(notify=False)` mas `ChatActivityLoaded`) para resumen,
   preview y contador de no leidos.
3. Mensaje XMPP en vivo (`MessageReceived(notify=True)`).
4. Carbons enviados/recibidos y eco de MUC, que tambien pueden duplicar un mensaje vivo.

Reglas obligatorias:

- `notify` controla voz NVDA, sonido y el incremento de no leidos. Un evento con
  `notify=False` puede persistirse y actualizar preview, pero no debe anunciarse ni marcar un
  chat como nuevo.
- El estado de no leidos que trae `ChatActivityLoaded.unread_count` es la autoridad del bridge
  durante el arranque. No lo sustituyas contando todos los mensajes historicos.
- Un mensaje historico de MUC puede llegar tarde como `groupchat_message`; el cliente lo marca
  `notify=False` si su timestamp es anterior al inicio de sesion.
- `MessageHistoryLoaded` nunca debe llamar directamente a NVDA o al sonido.
- Si un nuevo productor de eventos no puede distinguir vivo de historico, debe emitir
  `notify=False` y documentar la razon antes de conectarlo a UI.

### Arranque, cache y sincronizacion en segundo plano

El orden en `MainWindow` es deliberado:

1. `RosterLoaded` carga chats y ultimos mensajes desde SQLite.
2. Se activa `loading_initial_chat_activity` antes de `monitor_group_chats`.
3. Se unen los grupos cacheados con historial MUC desactivado (`maxhistory="0"`) y se cargan
   actividad/inbox en segundo plano.
4. Los `ChatActivityLoaded` se acumulan en `pending_chat_activity` hasta terminar la carga
   inicial o vencer el fallback de 8 segundos.
5. Se precargan hasta 20 chats con paginas pequenas y una cola de un chat a la vez.

No muevas `loading_initial_chat_activity` despues de `monitor_group_chats`: los grupos pueden
emitir mensajes inmediatamente al unirse y se anunciarian como nuevos. Tampoco conviertas la
precarga en una carga sin limite ni reemplaces cache visible con una respuesta remota vacia.

Al abrir un chat, primero se muestran mensajes cacheados y luego se pide historial. Al abrirlo
se marca como leido; al volver a la lista se limpia el marcador visual. Las actualizaciones de
fondo deben conservar seleccion, foco, orden legible y posicion de lectura.

### Grupos, identidad y ecos propios

- Los grupos del bridge suelen tener JID con `#` y usan el room archive MAM. No trates un JID
  con `+numero@...` como grupo solo por el dominio.
- Para identificar al participante, prioriza `muc#user item jid`; usa despues el recurso/nick
  del ocupante y finalmente un fallback tolerante. El nombre visible debe parecerse al nombre
  que el usuario tiene registrado, no depender ciegamente del nick tecnico.
- Una actualizacion de descubrimiento cuyo nombre sea solo el JID tecnico del grupo no puede
  reemplazar un titulo de grupo ya conocido desde roster, cache o una actualizacion anterior.
- La clasificacion de saliente en MUC debe aceptar JID local y nick propio con comparacion
  tolerante a mayusculas y acentos, pero nunca asumir que todo lo que aparece en un grupo es
  entrante.
- Un envio de texto crea primero un mensaje optimista local (`sender_jid="me"`, sin
  `message_id`). El eco puede regresar como `#room@dominio/recurso`. Solo se fusiona ese eco
  cuando coincide grupo, cuerpo, adjuntos compatibles y una ventana corta de 10 segundos.
- No deduplica todos los mensajes salientes ni todos los mensajes con el mismo texto. El grupo
  personal `Yo` puede recibir respuestas de Zapia que el bridge representa como salientes con
  `message_id` propio; esos mensajes deben conservarse y seguir siendo legibles.
- No uses `is_self_group` como unica prueba para borrar, silenciar o deduplicar. Es metadata del
  bridge y puede faltar o llegar tarde.
- Un resultado MAM cuyo XML sea `groupchat` debe conservar el JID del room. Si aparece durante
  una consulta de chat individual, se descarta de esa conversacion; no se debe pintar con el
  nombre o el nick del contacto individual.
- Al entrar a un grupo, Slidge llena el roster MUC con una presencia inicial por participante.
  El cliente toma esa foto completa una vez, extrae el JID real y el nick, y la guarda por lote en
  SQLite; no consulta la red al escribir una mencion.
- El autocompletado de menciones se activa con `@` dentro del compositor de un grupo. Busca sin
  distinguir tildes en el nombre personalizado y el nick de WhatsApp, pero inserta el nick MUC
  sin el `@`. El cliente adjunta ademas referencias XEP-0372 con JID y rango; el bridge debe tener
  aplicado `tools/patch_slidge_whatsapp_mentions.py` sobre su fuente Slidge para convertir esas
  referencias en `MentionedJID` nativo de WhatsApp. No cambies ese detalle por `@nick`, pues el
  parser de compatibilidad de Slidge espera el nick sin prefijo.
- Desde el 13 de julio de 2026, `marco-vps` usa la imagen
  `ghcr.io/marcomolinaleija/cliente-xmpp-bridge:puente-completo-20260713` con menciones,
  conversión de stickers y reenvíos nativos ya incorporados. Los reenvíos se transportan con
  `<forwarded xmlns="urn:marco-ml:whatsapp:forwarded:0"/>`. El cliente conserva esa bandera y
  XEP-0449 (`urn:xmpp:stickers:0`) en mensajes vivos, inbox, MAM y SQLite. La UI presenta
  `Reenviado`, permite elegir otro chat desde el menú contextual y ofrece envío explícito de
  stickers; no repitas estos parches en el puente. En otra instalación, configura esa etiqueta,
  ejecuta `docker compose pull
  slidge-whatsapp` y recrea únicamente ese servicio con `docker compose up -d --no-deps
  --force-recreate slidge-whatsapp`.

### Notificaciones y accesibilidad

- Las notificaciones de texto usan `_speak_incoming_message` y las de audio usan el mismo
  control de mute; ambas deben respetar `message.outgoing`, `notify` y
  `notifications_muted`.
- No generes tooltips con el cuerpo completo de un mensaje ni autoajustes una lista de cientos
  de filas con `LIST_AUTOSIZE`. NVDA puede bloquearse durante varios segundos con textos de
  miles de caracteres.
- La lista de mensajes mantiene una columna estable y ofrece el lector detallado con Enter.
  No muevas el foco en actualizaciones de fondo ni reconstruyas la lista mientras el usuario la
  esta leyendo si puede evitarse.
- Cambios de UI deben probar teclado, Escape, Enter, flechas, lector de mensaje, foco de lista
  y lectura NVDA con chats de mas de 300 mensajes y textos largos.

### Audio y multimedia

- Un audio entrante puede ser `.m4a`/AAC de WhatsApp (`audio/mp4`) o OGG/Opus. No asumas que
  una nota de voz siempre termina en `.ogg`.
- Al recibirse un mensaje con `media_kind="audio"` y `audio_url` o `media_url`, `MainWindow`
  inicia descarga automatica aunque el usuario no haya pulsado reproducir.
- Los stickers entrantes llevan XEP-0449, se conservan como `Message.is_sticker` y se descargan
  automáticamente para disponer de archivo local y miniatura sin anunciar el nombre hash. Si
  el bridge pierde el marcador al convertirlos, el cliente admite como fallback únicamente un
  WebP de imagen cuyo nombre sea el SHA-256 generado por el bridge, incluido el sufijo local
  ` (N)` por colisión; no clasifiques todos los WebP como stickers.
- Toda fila sin miniatura debe usar explícitamente el índice de imagen `-1` en `wx.ListCtrl`.
  Dejar el índice implícito puede reutilizar la primera miniatura en mensajes de texto.
- Las descargas escriben a `.part` y solo hacen `replace` al terminar. Persiste
  `media_local_path` despues de una escritura valida y conserva esa ruta en upserts posteriores.
- La reproduccion de audio debe usar solo `local_media_path(message)`. Si aun no existe,
  solicita/espera la descarga y reproduce al terminar; no hagas fallback a streaming HTTP,
  porque provoca cortes y ruido.
- La reproduccion de video usa una ventana nativa de libmpv. Al crear el reproductor de video
  deben habilitarse `input-default-bindings=yes` e `input-vo-keyboard=yes` antes de inicializar
  libmpv y enlazar explicitamente Space, Up/Down, Left/Right y Alt+F4 para controlar pausa,
  volumen, saltos de 5 segundos y cierre de la ventana. El cliente debe consumir
  `MPV_EVENT_SHUTDOWN` y llamar `mpv_terminate_destroy` para que Escape y Alt+F4 destruyan
  tambien la ventana nativa, no solo detengan la reproduccion.
- Para diagnosticar un audio, comprueba primero ruta local, tamano, `ffprobe` y decodificacion
  `ffmpeg`; no culpes al bridge sin distinguir archivo corrupto de streaming inestable.
- El audio enviado desde el cliente se normaliza a OGG/Opus; no reutilices esa regla para
  interpretar automaticamente los audios entrantes.

### Deduplicacion y persistencia

- Usa `message_id` cuando exista, pero admite que MAM, carbon, inbox y MUC pueden usar IDs
  distintos para la misma entrega. Los fallbacks de payload deben comparar timestamp,
  remitente, cuerpo, multimedia y cita sin fusionar conversaciones distintas.
- Conserva metadata enriquecida: un evento posterior puede traer menos datos que el cache
  (por ejemplo, perder `media_local_path` o `reply_quote`). Los upserts deben completar, no
  degradar, esos campos.
- Los estados de entrega son monotónicos: `pending -> sent -> delivered/received ->
  read/displayed`. Un carbon, eco o resultado MAM con `sent` no puede degradar un estado
  superior; `delivery_state` se persiste en SQLite para conservarlo al reabrir el chat.
- El preview y la hora de un chat solo avanzan con mensajes mas recientes; una pagina vieja no
  debe pisar el preview nuevo.
- SQLite usa WAL y migraciones defensivas. No borres mensajes ni reinicialices la base para
  resolver un bug sin confirmacion explicita.

### Integraciones especiales

`cliente_xmpp/integrations/rayoai.py` y el grupo personal con Zapia son un caso de uso del
usuario, no una regla para todos los grupos. Los mensajes que el bot devuelve por el bridge
pueden verse como `outgoing=True`; no los filtres por esa bandera. La regla de eco propio debe
ser estructural (room JID, ID y ventana temporal), no textual (por ejemplo, no filtres todo lo
que contenga "Zapia").

La acción `Describir con RayoAI` usa el IPC local de RayoAI para Windows, no el bridge XMPP.
Entrega la ruta local original, incluidos stickers WebP, y hace la llamada al socket en un hilo
de fondo para no bloquear wx. RayoAI es responsable de admitir y decodificar esos formatos.

### Estado de tests y diagnostico

Actualmente existe `tests/test_group_helpers.py`. El ciclo minimo es:

```powershell
conda activate XMPP
python -m compileall cliente_xmpp tests
python -m ruff check .
python -m unittest tests.test_group_helpers
python -m unittest tests.test_whatsapp_message_features
git diff --check
```

Para diagnosticar una incidencia real, inspecciona sin modificar la base local:

```powershell
python inspect_db.py
```

La base de prueba/usuario esta en `%USERPROFILE%\\.cliente-xmpp\\messages.sqlite3`; no copies
credenciales ni contenido sensible a commits, logs o issues. Para comparar un duplicado,
registra como minimo `chat_jid`, `message_id`, `sender_jid`, `outgoing`, `chat_is_group`,
`sent_at`, `body` truncado y rutas multimedia.

## Historial y sincronizacion

El historial se carga con paginas pequenas. Mantener esto es importante para que la app no se
congele al abrir chats con muchos mensajes.

Reglas:

- La apertura de un chat debe mostrar primero lo que ya esta en memoria/cache.
- La carga remota debe complementar, no borrar, la lista visible.
- La sincronizacion en segundo plano no debe robar foco ni cambiar el estado visible de forma
  brusca.
- Si se reciben mensajes antiguos despues de mensajes nuevos, fusiona por clave y ordena por
  timestamp sin degradar previews.
- El boton de "cargar anteriores" debe representar el estado real: disponible, cargando o
  agotado.

## Mensajes, respuestas y multimedia

Un `Message` representa el contenido normalizado que entiende la UI. Puede contener:

- `body`: texto visible del mensaje.
- `reply_quote`: texto citado ya separado del body.
- `audio_url`: compatibilidad con reproduccion de audios.
- `media_url`, `media_kind`, `media_mime`, `media_filename`, `media_local_path`: datos de
  adjuntos detectados o descargados.
- `message_id`: identificador remoto cuando exista.
- `outgoing`: si el mensaje fue enviado por la cuenta local.

Para respuestas/citas, separa el texto real del mensaje y la cita. La UI debe renderizarlo de
forma consistente, por ejemplo: `usuario, mensaje, respondiendo a: cita`.

Para editar mensajes de WhatsApp usa XEP-0308 (`urn:xmpp:message-correct:0`): reenvia el
cuerpo completo con un ID nuevo y `<replace id="id-original"/>`. Solo se ofrece para mensajes
propios de texto, ya enviados y con menos de 15 minutos; conserva el ID y hora del original,
marca el mensaje como editado y no lo muestres como un duplicado al recibir su correccion.

Para multimedia, soporta como minimo URLs OOB, URLs embebidas en body y metadatos compatibles
con los XEPs usados por bridges modernos. Si implementas descargas, no bloquees la UI y guarda
la ruta local solo cuando el archivo se haya escrito correctamente.

## Reglas de arquitectura

- Mantener separacion estricta entre UI, XMPP, storage y modelos.
- No importar wxPython desde `xmpp/`, `storage/` ni `models/`.
- No importar slixmpp desde `ui/`, `storage/` ni `models/`.
- No importar SQLite desde `ui/` salvo a traves de clases de `storage/`.
- Preferir eventos tipados para cruzar capas.
- Evitar funciones que mezclen parsing de protocolo, persistencia y renderizado.
- Evitar archivos demasiado grandes. Si una clase supera claramente una responsabilidad,
  extrae helpers o componentes nuevos.
- Extraer codigo cuando reduzca complejidad real, no por estetica.
- Mantener nombres de funciones orientados al dominio: `load_history`, `upsert_messages`,
  `set_messages`, `send_reply`.
- No crear abstracciones genericas si solo hay un caso de uso concreto.

## Tamanos y organizacion de archivos

Estas no son reglas matematicas, pero sirven como guia:

- Modulos de UI: dividir cuando un archivo acumule varios paneles o dialogos independientes.
- Modulos de protocolo: separar helpers si el parsing XML crece mucho o cubre varios XEPs.
- Storage: separar migraciones, queries o repositorios si SQLite sigue creciendo.
- Evitar archivos nuevos de mas de 400-600 lineas salvo que haya una razon fuerte.
- Evitar funciones de mas de 40-80 lineas; si ocurre, busca pasos con nombres claros.
- Preferir archivos pequenos con una responsabilidad sobre archivos "utils" enormes.

## Buenas practicas de codigo

- Escribe Python 3.12 idiomatico.
- Usa type hints en APIs internas nuevas.
- Mantener `ruff` limpio.
- Mantener `compileall` limpio.
- Usa dataclasses para datos simples.
- Usa context managers para recursos como conexiones SQLite.
- Captura excepciones en bordes de capa, no escondas errores dentro de logica central sin razon.
- No uses sleeps en UI para sincronizacion.
- No hagas trabajo de red o disco pesado en handlers de teclado o botones.
- No dupliques estado si puedes derivarlo barato y de forma confiable.
- Cuando agregues persistencia, piensa en idempotencia, migracion y compatibilidad con datos
  ya existentes.
- Cuando agregues UI, piensa en teclado, foco, lector de pantalla y textos claros.

## Practicas de accesibilidad

La aplicacion se usa con teclado y lector de pantalla. Cualquier cambio de UI debe cuidar:

- Orden de foco predecible.
- Etiquetas legibles en controles.
- Mensajes listados de forma completa cuando el usuario necesita leerlos.
- Atajos que no bloqueen flujos existentes.
- No mover foco por actualizaciones de fondo.
- Evitar texto truncado en vistas de lectura detallada.

Si una actualizacion automatica llega mientras el usuario lee un chat, no debe interrumpir la
lectura ni desplazarlo de forma inesperada.

## Persistencia y consistencia

Al tocar `message_store.py`:

- Mantener `PRAGMA journal_mode = WAL` salvo razon clara para cambiarlo.
- Las claves de mensaje deben resistir reentregas del mismo contenido.
- Prioriza `message_id` cuando exista; usa hash estable como fallback.
- Nunca borres cache del usuario sin confirmacion explicita.
- Al modificar schema, agrega migraciones defensivas y defaults.
- Los previews de chat se derivan del mensaje mas reciente conocido.
- Si un update trae timestamp anterior al preview actual, no debe reemplazarlo.

## XMPP y protocolos

Antes de cambiar parsing o envio XMPP, revisa el flujo actual y documentacion del protocolo
relevante. Este proyecto ya contempla MAM para historial y formatos usados para archivos o
multimedia como OOB/HTTP URLs y metadatos de comparticion de archivos.

Fuentes oficiales del bridge usado:

- Slidge: https://slidge.im/
- Documentacion de slidge-whatsapp: https://slidge.im/docs/slidge-whatsapp/main/
- Configuracion inicial de slidge-whatsapp como componente XMPP:
  https://slidge.im/docs/slidge-whatsapp/main/admin/quickstart.html
- Adjuntos en slidge-whatsapp y uso de XEP-0363:
  https://slidge.im/docs/slidge-whatsapp/main/admin/attachments.html

Fuentes oficiales de XMPP y XEPs relevantes para este cliente:

- Indice de extensiones XMPP: https://xmpp.org/extensions/
- XEP-0030, Service Discovery: https://xmpp.org/extensions/xep-0030.html
- XEP-0045, Multi-User Chat: https://xmpp.org/extensions/xep-0045.html
- XEP-0050, Ad-Hoc Commands: https://xmpp.org/extensions/xep-0050.html
- XEP-0060, Publish-Subscribe: https://xmpp.org/extensions/xep-0060.html
- XEP-0249, Direct MUC Invitations: https://xmpp.org/extensions/xep-0249.html
- XEP-0280, Message Carbons: https://xmpp.org/extensions/xep-0280.html
- XEP-0297, Stanza Forwarding: https://xmpp.org/extensions/xep-0297.html
- XEP-0313, Message Archive Management: https://xmpp.org/extensions/xep-0313.html
- XEP-0363, HTTP File Upload: https://xmpp.org/extensions/xep-0363.html
- XEP-0402, PEP Native Bookmarks: https://xmpp.org/extensions/xep-0402.html
- XEP-0444, Message Reactions: https://xmpp.org/extensions/xep-0444.html
- XEP-0461, Message Replies: https://xmpp.org/extensions/xep-0461.html
- XEP-0428, Fallback Indication: https://xmpp.org/extensions/xep-0428.html

No asumas que todos los bridges emiten exactamente el mismo XML. Implementa parsing tolerante:
leer namespaces conocidos, usar fallback razonable y conservar body si no se puede extraer algo
mejor.

## Testing y validacion

Antes de finalizar cambios de codigo, ejecutar cuando aplique:

```powershell
conda activate XMPP
python -m compileall cliente_xmpp
python -m ruff check .
git diff --check
```

Si agregas tests en el futuro, documenta aqui el comando exacto. Actualmente no hay directorio
de tests dedicado en el repositorio.

Para cambios de UI, valida manualmente los flujos afectados:

- Login/conexion.
- Lista de chats.
- Apertura de chat.
- Carga de mensajes cacheados y remotos.
- Mensajes entrantes en vivo.
- Respuestas/citas.
- Foco, Escape, Enter y lectura con lector de pantalla.

## Git y cambios

- Mantener commits pequenos y coherentes.
- No mezclar refactors grandes con fixes de comportamiento.
- No revertir cambios ajenos sin confirmacion.
- Antes de commitear, revisar `git status --short` y `git diff --check`.
- Los mensajes de commit de este proyecto deben ir en español.
