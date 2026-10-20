# Vinculación recuperable de WhatsApp

El cliente distingue registro XMPP, emparejamiento, conexión autenticada y
sincronización. Escanear un QR no habilita el envío: el puente debe confirmar
la autenticación. Los chats y las credenciales existentes no se borran para
resolver un error de vinculación.

## Uso

1. En una instalación local, CAN comprueba la versión antes de ofrecer la
   vinculación. Si el appliance incluye v19, ofrece actualizar con confirmación
   explícita, respaldo y rollback; no actualiza silenciosamente. El mínimo
   compatible con el flujo heredado es v35.
2. Una cuenta nueva completa primero el registro del gateway. Después permite
   escanear el QR o pedir un código por número internacional.
3. El código aparece en CAN, no llega por SMS. En el teléfono se introduce desde
   **Dispositivos vinculados > Vincular un dispositivo > Vincular con número de
   teléfono**. No depende de recibir una notificación.
4. Cerrar la ventana QR no cancela el intento. **Cancelar vinculación** pide su
   cancelación al puente y espera confirmación. Una respuesta perdida no da por
   cancelado el intento ni permite crear otro encima.
5. Una vez autenticado, los contactos y grupos se sincronizan por separado. Un
   error de sincronización no demuestra que se haya perdido la vinculación.

## Contrato del puente

El componente anuncia `urn:can:whatsapp:linking:1` mediante XEP-0030. El cliente
consulta IQ al JID exacto del componente. No confía en el listado de comandos
como prueba de autenticación ni en presencias heredadas cuando existe el contrato.

| Operación | Semántica |
| --- | --- |
| `status` (IQ get) | Snapshot de la cuenta XMPP solicitante; no genera QR ni inicia sesión. |
| `start` (IQ set) | Inicia o adopta un único intento; `request` permite repetir un IQ sin duplicarlo. |
| `phone` (IQ set) | Exige el intento actual y espera preparación nativa de su WebSocket. |
| `cancel` (IQ set) | Exige el intento actual; desconecta el intento sin llamar a `Logout` ni borrar credenciales. |

La respuesta incluye versión, intento, revisión, fase, autenticación y fase de
sincronización. Cada QR tiene revisión y vigencia restantes; la PNG se transporta
en el IQ, sin depender del servidor HTTP de adjuntos. La vigencia se reduce por
el tiempo de ida y vuelta. QR y código son secretos: no deben copiarse a logs,
informes, fixtures reales ni diagnósticos compartidos.

El cliente descarta respuestas de otra cuenta, componente, conexión o revisión
anterior. Los eventos nativos de un cliente retirado se descartan en el puente.
Las descargas heredadas comprueban el contexto antes de abrir la ventana.

### Compatibilidad y errores

Los servidores sin la extensión conservan el flujo XEP-0050, con las barreras de
autoridad y cancelación del cliente. No disponen de todas las garantías del nuevo
contrato: para obtenerlas hay que instalar una imagen que anuncie la extensión.
El número de imagen por sí solo no acredita esa capacidad.

`client_outdated`, `stream_replaced`, `unsupported_pairing`, `cancel_failed`,
`stale_attempt` y `status_unavailable` producen acciones y mensajes distintos.
No hay recuperación automática mediante `wa_logout`, esperas fijas para adivinar
preparación, borrado de SQLite ni desvinculación de dispositivos humanos.

## Construcción y despliegue

`tools/Dockerfile.bridge-linking` deriva de la imagen personalizada existente.
Su valor de `BRIDGE_IMAGE` es una base local de desarrollo; antes de desplegar se
debe comparar su ID inmutable con la imagen activa y revisar los mounts efectivos.
No sustituirla por una imagen genérica perdiendo voz, medios, lecturas o identidad.

El parche mantiene la API gopy y sus hooks anteriores. Las pruebas nativas y race
se ejecutan sin cuentas; `tools/smoke_bridge_linking_runtime.py` comprueba la
biblioteca instalada, los callbacks reales y el aislamiento del endpoint sin red.
El parche de falso éxito de `re-login` se instala también en la imagen, aunque
el despliegue conserve un bind mount histórico equivalente.

Antes de cambiar producción: conservar imagen anterior, configuración, mounts y
un respaldo consistente de Slidge. Recrear sólo el servicio del puente, mantener
los mismos volúmenes y verificar arranque, imagen efectiva, extensión anunciada
y estado autenticado. Un smoke de servicios no acredita una vinculación real.
La publicación en GHCR, un appliance nuevo y el binario Windows son entregas
separadas, no efectos secundarios de este despliegue.

## Verificación

### Carga masiva sin bloquear la vinculación

Los eventos de mensajes, historial y actividad se conservan en una cola FIFO.
El cliente procesa hasta 25 eventos o unos 8 ms por turno y cede 10 ms al bucle
wx antes de continuar. Una operación individual no se puede interrumpir con ese
presupuesto. Se agrupan los repintados, no los mensajes ni sus escrituras,
marcadores o notificaciones; `notify=False` sigue sin anunciar ni sumar no leídos.

La lista se ordena una vez por turno y conserva la selección y el orden bajo el
foco. Los estados de conexión, vinculación y voz usan eventos wx independientes:
no esperan detrás del historial y pueden cerrar un diálogo modal de código.
Un timeout o rechazo del permiso de voz cierra sólo ese transporte y comunica el
fallo; no invalida una sesión WhatsApp autenticada ni solicita otro QR.

Estos cambios son del cliente Python. No requieren reiniciar el puente ni
reemplazar la imagen productiva. Para probar la fuente desde la raíz se puede
usar `conda run --no-capture-output -n XMPP python -m cliente_xmpp.app.main`.

### Comprobaciones por capa

- Python: `conda run -n XMPP python tools/run_tests.py --suite all`.
- Foco: añadir `--match test_whatsapp_linking` para el contrato y aislamiento.
- Estática: `conda run -n XMPP python -m ruff check .` y `git diff --check`.
- Imagen: Go, race y smoke offline del Dockerfile, sin volúmenes de producción.
- Manual: cuenta nueva por QR y número; cerrar/reabrir; cancelación; QR rotado;
  desconexión antes/después del escaneo; reapertura tras reiniciar Windows;
  pérdida de sesión desde el teléfono; envío/recepción; foco, Escape, Enter y NVDA.

Una cuenta real sólo se desvincula por acción o autorización específica de su
titular. La aprobación de pruebas offline no sustituye esa comprobación manual.

### Evidencia del 20 de octubre de 2026

- Rama `vinculación`, basada en `a16afa2`; cliente disponible desde el código
  fuente, sin construir ni publicar un ejecutable Windows.
- Catálogo Python completo: 1,038 métodos y 525 subcasos aprobados en procesos
  por área. La unión de sus IDs coincide exactamente con la selección `all`.
  La última comprobación de conexión y plataforma suma 263 métodos y 134
  subcasos. Ruff y `git diff --check` aprobados.
- La ejecución monolítica reproduce un access violation de wx al combinar
  fixtures nativos. Se reprodujo también en una copia intacta de `a16afa2`;
  no se eliminó ningún escenario ni se modificó el producto de programados
  para ocultarlo. Este fallo de ejecución compartida sigue pendiente.
- Imagen productiva local `cliente-xmpp-bridge:linking-20261020`, ID
  `sha256:c52903bb7ce1ac8c7417279227de330bbc7c2ec693e0ea0522c7757ba0fcfed2`.
  Cero reinicios tras el despliegue; misma configuración de voz y mismos mounts.
- 81 pruebas Go y 81 con race aprobadas. Smoke offline sobre la biblioteca y
  callbacks reales, aislamiento IQ y generación de una PNG QR ficticia con
  vigencia correcta. Otros 1,311 archivos de los paquetes coinciden con la
  imagen anterior, incluido el helper de voz.
- Consulta XMPP autenticada real: contrato de vinculación versión 1,
  WhatsApp autenticado, sincronización lista y capacidad de voz anunciada.
  La sonda envía presencia XMPP normal, pero no ejecuta start, phone, cancel
  ni desvinculación; no imprime cuentas, códigos, imágenes ni credenciales.
- Respaldo consistente previo al cambio y rollback disponibles bajo
  `/opt/xmpp/backups/linking-20261020T204957Z`; ambas SQLite aprobaron
  `quick_check`. La primera intervención se revirtió automáticamente porque
  el verificador omitía stderr, donde Slidge escribe su señal de arranque;
  se corrigió el verificador antes del despliegue definitivo. No se restauraron
  bases sobre datos nuevos ni se reiniciaron Prosody o el servicio de adjuntos.

Queda al titular probar una desvinculación y vinculación nuevas, QR y teléfono,
con el cliente de esta rama. No se realizó esa acción en su nombre, ni se
certificó una llamada audible o una prueba humana de NVDA.

### Verificación adicional de la carga por lotes

- Catálogo Python actual completo: 1,051 métodos y 534 subcasos, sin fallos,
  errores ni omisiones, en procesos por áreas. Se comprobó que la unión de IDs
  coincide exactamente con `all`; no se afirma resuelto el crash monolítico
  preexistente de wx descrito arriba.
- Cola sintética de 5,000 eventos en FIFO; 100 mensajes conservan sus actualizaciones
  y la semántica de notificaciones. En la lista con foco, 1,000 actualizaciones
  se agrupan en una sola escritura nativa, sin cambiar selección ni orden focal.
- Un bucle wx nativo atiende un timer antes de terminar 1,200 eventos pendientes.
  Las pruebas de voz cubren timeout, rechazo y respuestas de una activación vieja.
- Ruff y `git diff --check` aprobados. Estos checks no sustituyen repetir la carga
  real con teclado y NVDA. Esta corrección no modificó ni reinició producción,
  no desvinculó cuentas y no construyó ni publicó un binario Windows.

### Recepción tardía y congelamiento: diagnóstico posterior

La prueba humana posterior siguió mostrando congelamiento y mensajes tardíos.
Las comprobaciones anteriores del pump no acreditan que ese problema esté resuelto.

- Una carga aislada, sin red, medios, reacciones ni NVDA, con 500 mensajes en
  caché y 1,000 mensajes nuevos, permitió reproducir trabajo excesivo en
  `_merge_messages`: se repetía la deduplicación de todas las filas existentes
  por cada stanza. El cliente conserva ahora una salida canónica para anexar
  mensajes cronológicos nuevos, con fallback completo ante colisiones de
  identidad, contenido o eco, correcciones, retracciones e historial tardío.
  Las llamadas y cargas de archivos quedan fuera de esa ruta rápida.
- El índice temporal de contenido descarta sólo candidatos de búsqueda fuera
  de su ventana; no elimina mensajes ni reduce el historial. Conserva los
  candidatos cuyo timestamp puede cambiar por una llamada o carga de archivo.
- Con el mismo profiler y SQLite temporal real, el procesamiento pasó de 200 a
  953 eventos en una ventana de 20 segundos. Es una comparación instrumentada,
  no una certificación de rendimiento normal ni de recepción WhatsApp real.
  Otra ejecución sin profiler completó los 1,000 eventos en 11.36 segundos y
  verificó 1,000 mensajes nuevos persistidos, siempre con datos ficticios.
- La validación actual cubre exactamente los 1,057 métodos y 536 subcasos del
  catálogo mediante procesos por área y, para plataforma, por módulo. Todos esos
  escenarios aprobaron, junto con Ruff y `git diff --check`. La ejecución de
  plataforma combinada volvió a terminar con `0xC0000005`; no se considera
  resuelto ese fallo de ejecución compartida ni se omiten sus escenarios.
- Una correlación por identidad, sin exportar cuerpos ni identificadores,
  ubicó un mensaje de texto en el archivo del servidor unos 314 segundos después
  de su timestamp WhatsApp. La persistencia local coincidió con el archivo del
  servidor dentro de la precisión de un segundo. Esa demora concreta es anterior
  al cliente; no debe atribuirse sólo al renderizado de wx.
- En la fuente productiva del puente, el dispatcher global ejecuta callbacks
  serialmente. `make_sync` espera `future.result()` de la corutina entera, que
  puede esperar contactos, bookmarks o trabajo remoto. Historial y mensajes
  vivos comparten esa cola. Esto permite bloqueo de cabecera entre usuarios;
  falta una captura durante carga que atribuya la demora observada a una función.
- Los parsers nativos descargan adjuntos antes de propagar el mensaje. Un fallo
  de descarga devuelve `EventUnknown`, descartando también el evento del
  mensaje. Se observaron errores HTTP 403 productivos. Esto es un fallo separado,
  no una prueba de que todos los mensajes de texto se pierdan por ese motivo.

### Corrección del puente desplegada

El nuevo overlay `tools/Dockerfile.bridge-inbound` se construye sobre la imagen
de vinculación existente; no sustituye personalizaciones de voz, stickers,
medios, identidad o lectura. No cambia el esquema ni elimina dispositivos.

El builder local se obtiene de la etapa `builder` de
`tools/Dockerfile.bridge-linking`, etiquetada como
`cliente-xmpp-bridge:linking-builder-20261020`. El nuevo Dockerfile reutiliza
sus dependencias y cachés de compilación. Estas etiquetas son locales a esta
intervención; no son una publicación ni un canal de actualización del cliente.

| Ruta por sesión | Límite | Comportamiento |
|---|---|---|
| QR, emparejamiento, conexión y cierre | 16 callbacks | Independiente de las esperas de datos |
| Mensajes vivos y datos fiables | 128 trabajos | FIFO y backpressure, nunca descarte por saturación |
| Historial | 2 lotes | Serial, descargado y entregado fuera de la ruta viva |
| Presencia y chat-state | 64 callbacks | Best-effort, sin ocupar capacidad fiable |

Los callbacks conservan la propiedad síncrona del payload Go hasta que termina
Python. No se crean tareas ilimitadas ni una goroutine por mensaje. Cada lane
tiene un consumidor y los consumidores de otras cuentas son independientes.
La cancelación explícita detiene esperas obsoletas sin cancelar las futuras
compartidas de contactos y bookmarks. Una caída transitoria no las cancela.
Los eventos de llamadas esperan esas futuras y permanecen en datos, no en la
lane de vinculación; su observación nativa de recuperación conserva su lugar
anterior al encolado.

La descarga tiene un plazo de 30 segundos. Un error de medio conserva identidad,
timestamp, caption y contexto de respuesta y entrega una indicación explícita
de adjunto no disponible. No inventa un archivo válido ni cierra la sesión.
Esto no recupera automáticamente medios antiguos ya inaccesibles.

También se capturó el hilo Python productivo, con GIL activo, copiando stanzas
en `LegacyMUC.__old_school_history`. La entrega automática al entrar al grupo
no tenía límite por defecto ni cedía al bucle de eventos. Ahora entrega hasta
50 mensajes por defecto y cede cada cinco; un límite cero no consulta el
archivo y una petición positiva explícita se conserva. MAM y el historial
almacenado no se recortan. Esta política de entrega es independiente de la
retención, como permite [XEP-0045, historial de entrada](https://xmpp.org/extensions/xep-0045.html#enter-history).

La imagen final pasó 86 pruebas Go y 86 con race. El smoke nativo usó dos
cuentas, historial bloqueado y 200 mensajes vivos; la inyección de prueba está
excluida del binario final. El smoke del método MUC instalado comprobó cero
mensajes sin consulta, 50 por defecto, 100 solicitados y un heartbeat después
del quinto mensaje. Las pruebas no montaron datos productivos ni se conectaron
a WhatsApp.

#### Primer despliegue del 20 de octubre de 2026 (sustituido por la corrección inferior)

- Imagen local VPS `cliente-xmpp-bridge:inbound-20261020`, ID
  `sha256:d759d2aedc0684ea2cd166d40d7d5753fde783830b8371590eff11f035405eae`.
- Consulta autenticada: contrato 1, WhatsApp autenticado, fase `connected`,
  sincronización `ready` y capacidad de voz anunciada. Cero reinicios del
  contenedor definitivo. No se certifica con ello una conversación o llamada
  real de extremo a extremo.
- 1,649 archivos de los paquetes permanecen idénticos; sólo cambian los siete
  archivos autorizados del overlay, incluido el binario nativo y el método MUC.
- Cuatro montajes, credenciales/configuración, comando, usuario y otros
  servicios intactos. No se reiniciaron Prosody ni el servicio de adjuntos.
  El respaldo definitivo está en `/opt/xmpp/backups/inbound-20261020T232439Z`;
  las tres SQLite aprobaron `quick_check`. No se restauraron bases sobre datos
  posteriores ni se desvincularon dispositivos.
- Hubo dos reversiones de protección: una comparación incorrecta del orden
  de variables de entorno y una validación sin disponibilidad XMPP que agotó
  su plazo. Se corrigió la comparación a mapas y se automatizó la aprobación.
  La sonda usa un recurso único de prioridad negativa: mantiene disponibilidad
  para este gateway sin reemplazar CAN ni competir con su prioridad para
  mensajes al JID bare. No ejecuta comandos de emparejamiento.
- Muestra pasiva final de dos segundos: 2% de CPU y 199.5 MiB RSS del proceso
  del puente. No representa una garantía para toda carga ni mide el cliente.

Las comprobaciones Python por área y módulo conservan su limitación nativa de
wx: una nueva ejecución del módulo del pump terminó con código -1 después de
registrar sus 11 métodos sin fallos en JSON. No se cuenta ese cierre como una
ejecución aprobada ni se afirma reparado el problema nativo documentado arriba.
La última suite `bridge` pasó 166 métodos y 93 subcasos; `test_ui_performance`
pasó 36 métodos y 17 subcasos, incluido el flujo real de siete avisos. El catálogo
actual contiene 1,066 métodos; no confundir ese inventario con una ejecución
monolítica aprobada. Ruff y `git diff --check` aprobaron.

#### Regresión del pool SQL y corrección posterior

El primer overlay introdujo una regresión en el historial al unirse a grupos:
`MessageArchive.get_all()` mantiene una sesión SQL dentro de su generador.
El nuevo `await` cada cinco mensajes cedía ejecución sin agotar ese generador.
Varios grupos retenían conexiones simultáneamente hasta agotar el pool de
5 conexiones más 10 adicionales. Producción registró repetidos
`sqlalchemy.exc.TimeoutError`, con esperas síncronas de 30 segundos en el bucle
principal. Esto podía atascar tanto la recepción como el procesamiento de
envíos. La comprobación inicial con un archivo ficticio no modelaba ese recurso.

La versión 2 materializa la página solicitada con `list(get_all(...))` antes de
enviar o ceder ejecución. Conserva límites, orden, marcas de tiempo y MAM; no
aumenta el pool. La prueba reforzada utiliza el `MessageArchive.get_all()`
instalado, sesiones ORM y un `QueuePool` real, sin datos productivos ni red:

- La imagen anterior falla por retener una conexión durante el envío.
- La corregida procesa 20 grupos concurrentes y 1.000 mensajes con un máximo
  de una conexión ocupada; ninguna permanece ocupada durante los envíos ni al
  finalizar. Se conservan las comprobaciones de cero, 50 y 100 mensajes y yield.
- Las siete pruebas de `test_bridge_inbound`, Ruff focalizado y
  `git diff --check` aprobaron. No se repitieron pruebas nativas Go porque el
  binario no cambió.

Se desplegó `cliente-xmpp-bridge:inbound-pool-20261020`, ID
`sha256:1120cc79e8413fa455da3e84ac4991b83a4f03cf1326bd4ddf495044d415c182`,
iniciada a las 23:43:40 UTC. Sólo cambia `slidge/group/room.py` dentro de los
paquetes frente a la imagen anterior; otros 1.655 archivos permanecen idénticos.
El respaldo privado es `/opt/xmpp/backups/inbound-pool-20261020T234318Z` y las
tres SQLite aprobaron `quick_check`. Se conservaron montajes, configuración y
credenciales, sin restaurar bases, desvincular dispositivos ni reiniciar los
otros servicios. La receta incremental es
`tools/Dockerfile.bridge-inbound-hotfix`; la receta completa también aplica v2.

A las 23:45:56 UTC la consulta autenticada devolvió `connected`, `ready` y
contrato 1. Desde este arranque no se registraron errores del pool ni tracebacks,
y el contenedor no se reinició. **Esto no acredita entrega real de mensajes**:
falta confirmar entrada y salida con el cliente. El estado local `sent` se emite
tras `msg.send()`, no tras una confirmación de WhatsApp; no debe interpretarse
como entrega al teléfono. No se reenviaron automáticamente mensajes inciertos,
para no duplicarlos.

### Regresión de notificaciones corregida

La primera optimización de fusión anexaba filas a la lista canónica in-place.
`_store_message` comprobaba después si la fila pertenecía a esa misma lista:
los nuevos mensajes podían quedar clasificados como existentes y no anunciarse.
Ahora toma un snapshot de identidad de objetos antes de fusionar. La regresión
ejercita el almacenamiento y handler reales: siete mensajes nuevos producen
siete avisos, mientras que el duplicado y `notify=False` no producen ninguno.
El titular debe relanzar el proceso Python para tomar este cambio; no se cambia
el código del proceso que ya está abierto ni se construye un cliente Windows.

## Referencias

- [WhatsApp: vinculación por número](https://faq.whatsapp.com/1324084875126592/).
- [WhatsMeow: PairPhone](https://pkg.go.dev/go.mau.fi/whatsmeow#Client.PairPhone).
- [WhatsMeow: eventos de emparejamiento y conexión](https://github.com/tulir/whatsmeow/blob/main/types/events/events.go).
- [WhatsMeow: descarga y errores de medios](https://pkg.go.dev/go.mau.fi/whatsmeow#Client.Download).
- [SQLAlchemy: agotamiento del pool y liberación de sesiones](https://docs.sqlalchemy.org/en/20/errors.html#queuepool-limit-of-size-x-overflow-y-reached-connection-timed-out-timeout-z).
- [XEP-0050: cancelación](https://xmpp.org/extensions/xep-0050.html#usecases-cancel).
