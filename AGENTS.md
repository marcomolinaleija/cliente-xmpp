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
- Los mensajes de commit de este proyecto pueden ir en espanol si el usuario lo pide.

## Como debe trabajar un agente en este repo

1. Lee este archivo y el README.
2. Inspecciona los archivos relevantes antes de proponer cambios.
3. Entiende el flujo entre `xmpp/client.py`, eventos, `MainWindow`, paneles UI y storage.
4. Haz cambios pequenos y verificables.
5. Mantener compatibilidad con datos locales ya creados.
6. Valida con los comandos del entorno Conda.
7. Explica al usuario que cambio y como probarlo.

## Criterio principal

La prioridad del proyecto es una experiencia fluida, robusta y accesible para mensajes.
Cuando haya conflicto entre una implementacion rapida y una que preserve foco, cache,
sincronizacion y legibilidad, elige la segunda si sigue siendo razonablemente simple.
