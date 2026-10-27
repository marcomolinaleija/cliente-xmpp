# Recuperación del puente: imagen corregida y despliegue controlado

> **Confirmación humana, 23 de octubre:** el usuario informa que dejó CAN
> activo durante horas y que envía y recibe mensajes. Esto confirma uso real
> sostenido más allá de las sondas y autoriza conservar las correcciones con
> commit/push, **sin publicar en GHCR**. No especificó una matriz por tipo de
> conversación, llamadas o NVDA; esas comprobaciones no se dan por realizadas.

> **R6 aceptada, 22 de octubre, 22:21 UTC−06:00:** se corrigió el COMMIT
> bloqueante del eco grupal y está desplegada en producción. Completó diez
> minutos sin nuevos errores SQL/fatales/XML ni reinicios y ambas sondas están
> autenticadas en `connected/ready`. En ese cierre aún faltaba la confirmación
> humana de mensajería sostenida, recibida el 23 de octubre. El aviso local de CAN
> es independiente y requiere abrir el cliente desde el código actualizado.
> Las notas R5 y anteriores que siguen son la cronología del incidente.

> **Hallazgo adicional, 22 de octubre, 21:38 UTC−06:00:** se reprodujo y corrigió
> un falso «Reconectando» en la UI de CAN: una actualización de sincronización
> ya autenticada elegía ese texto aunque no hubiera desconexión. La corrección
> local requiere relanzar CAN desde el código actualizado. La sonda independiente
> confirmó el puente `connected/ready`; el bloqueo SQL grupal sigue siendo un
> defecto real separado, todavía pendiente. Véase el último apartado.

> **Recaída posterior, 22 de octubre, 21:27 UTC−06:00:** el usuario recibió
> mensajes unos tres minutos y volvió a ver «Reconectando con WhatsApp».
> **R5 sigue activa, sin reinicios**, pero reapareció un bloqueo SQL en el eco de
> un marcador de lectura grupal a las 21:25:03. Se reprodujo aisladamente que
> esa ruta bloquea asyncio al hacer COMMIT; aún no está corregida ni acredita
> por sí sola la causa única del aviso. La aceptación inicial de diez minutos
> no cubría este episodio. Véase «Recaída con R5 sin reinicio» al final.
> Los estados R2/R3/R4 de las secciones anteriores son evidencia histórica.

El 22 de octubre se construyó y desplegó una imagen incremental del puente,
conservando cuentas, sesiones, bases, TLS, voz y demás funciones. Se corrigieron
el panic al componer mensajes, dos lectores SQL retenidos y llamadas nativas
que bloqueaban asyncio; se añadió recuperación oficial de snapshots desde el
teléfono y se eliminó el reinicio por advertencias del watchdog.

La auditoría inicial y las limitaciones de wx están en
[el informe del cliente](AUDITORIA_RECONEXION_2026-10-22.md). Esta intervención
posterior sí contó con autorización para construir y sustituir el puente.

## Causas y correcciones comprobadas

| Problema | Prueba y solución acotada |
|---|---|
| Panic `getMessagePayload`, `session.go:723` | El código original fallaba al combinar contenido de distinto tipo: un payload existente no garantizaba que su submensaje estuviera inicializado. La matriz nativa de 60 combinaciones rechaza la fuente original. La corrección inicializa texto extendido cuando corresponde y construye la ubicación conservando citas/menciones, incluyendo coordenadas negativas. |
| SQLite `database is locked` | En la imagen anterior, `get_archived_messages()` mantenía el cursor y la conexión después de entregar el primer resultado; la escritura concurrente sobre una SQLite temporal real fallaba. Ahora materializa sólo los resultados del ID solicitado antes de cederlos. La validación HTTP de adjuntos también libera la sesión ORM antes de esperar red. No se amplió el pool ni se cambió el modo de las bases. |
| Asyncio bloqueado durante sincronización | `GetContacts(refresh=True)`, `GetGroups()` y las suscripciones de presencia hacían red desde el hilo asyncio. El smoke reproduce que una llamada nativa no puede completar su callback al mismo bucle bloqueado: cero recorridos completados en la base, cinco en la corregida. Se usa `asyncio.to_thread` directamente en los callers existentes. Esas rutinas Go toman un cliente estable, limitan el contexto a 30 segundos y descartan resultados de una sesión sustituida. |
| Snapshot LTHash sigue fallando tras un fetch completo | Las 110 advertencias previas se clasificaban como `snapshot_hash`, no como caída general. Si la recuperación completa falla por el error tipado LTHash, se solicita un snapshot al teléfono mediante `BuildAppStateRecoveryRequest` y `SendPeerMessage`. La solicitud es acotada y **no confirma lectura**: se conserva el error hasta que la operación pueda completarse. |
| Reinicios del watchdog por warnings | El script anterior sumaba errores de lectura, 503 y SQLite históricos. El nuevo sólo reinicia un contenedor detenido o con un healthcheck explícitamente `unhealthy`, conservando exclusión mutua, cooldown y presupuesto. No tener healthcheck no demuestra salud: un bloqueo silencioso requeriría una señal de aplicación adicional. |
| Rejoin MUC perdido al recrear el componente | La fuente instalada de Slidge envía presencia propia `110/332` al cerrar. CAN intentaba unirse de nuevo al segundo; si recibía un rechazo transitorio, borraba la unión y su ping periódico ya no revisaba ese grupo. La corrección del cliente permite cuatro reintentos, de 2/5/15/30 segundos, sólo por errores de enrutamiento transitorios. No reintenta bans, permisos ni errores genéricos. Los callbacks viejos no retiran ocupantes de la sesión nueva. |

No se introdujeron gestores de conexión, colas nuevas ni wrappers de servicio.
No se habilitó `SkipBrokenAppStatePatches` ni se usó
`BuildFatalAppStateExceptionNotification`: esta última desvincularía dispositivos.
La vía oficial de recuperación sigue dependiendo de que el teléfono responda;
no se afirma haber reparado el snapshot productivo sin observar esa respuesta.

## Imagen y receta

- Imagen final: `cliente-xmpp-bridge:recovery-r2-20261022`.
- ID: `sha256:6d7f941f720ea3d801b3c834e5c6ce3e9dadd2fa0ea7dc7d3bca17f3a0ed8f8e`.
- Base inspeccionada: `cliente-xmpp-bridge:inbound-pool-20261020`,
  `sha256:1120cc79e8413fa455da3e84ac4991b83a4f03cf1326bd4ddf495044d415c182`.
- Builder existente: `cliente-xmpp-bridge:linking-builder-20261020`; se conservaron
  las mismas fijaciones de módulos y toolchain de la imagen previa.
- Receta: `tools/Dockerfile.bridge-recovery`; aplicar este overlay al final de
  la cadena. No sustituye la imagen por un Slidge genérico ni actualiza librerías.

Construcción autorizada desde un contexto que contenga los archivos de `tools`:

```sh
docker build --progress plain -f Dockerfile.bridge-recovery \
  -t cliente-xmpp-bridge:recovery-r2-20261022 .
```

Los smokes del Dockerfile se ejecutan sin red y sin montajes productivos.
Se conserva la corrección anterior del historial MUC: 20 grupos y 1.000 mensajes
no retienen conexiones SQL durante el envío.
La comparación final encontró ocho archivos de paquetes modificados y 1.648
idénticos: tres módulos Python de roster/sesión, dos fuentes Go, la biblioteca
nativa y las dos rutas SQL corregidas. No se reemplazaron dependencias.

## Validación

| Comprobación | Resultado |
|---|---|
| Fuente real del paquete y parche | Anclas comprobadas antes de escribir; aplicación repetida idempotente |
| Baseline en imagen original | Cursor retenido, escritura bloqueada y conexión durante HTTP reproducidos; callbacks de roster no completan |
| Go final, paquete y `voicecore` | 83 métodos y 69 subcasos aprobados |
| Mismos Go con `-race` | 83 métodos y 69 subcasos aprobados |
| Omisiones Go declaradas por el códec | `TestSmplTablesCDF` y `TestSynth`; indican cobertura por sus pruebas de decodificación E2E, no fueron añadidas ni usadas para ocultar un fallo |
| Biblioteca instalada y runtime | Smokes de vinculación, lectores SQL, roster, watchdog e historial MUC aprobados |
| Watchdog con comandos simulados | Contenedor normal/sin healthcheck, healthy, starting, unhealthy, detenido y error de inspect; cooldown y presupuesto conservados |
| Python `--suite all --area bridge` | 168 métodos / 100 subcasos, aprobados |
| Python conexión/mensajes/historial tras corrección MUC | 378 métodos / 214 subcasos, aprobados |
| Ruff completo y `git diff --check` | Aprobados |

La suite Windows monolítica y el módulo wx del pump conservan el cierre nativo
documentado en la auditoría inicial. No se afirma arreglado ni se eliminaron
pruebas para obtener verde. Las pruebas de conexión no sustituyen NVDA ni una
confirmación humana de envío y recepción.

## Despliegue y reversión

La imagen final arrancó el **22 de octubre a las 08:11:47 UTC−06:00**.
Se verificaron sin cambios semánticos variables/credenciales, comando, usuario,
política de reinicio, puertos y los cuatro montajes: datos, adjuntos, parche de
comandos y TLS. Los IDs de los demás contenedores permanecieron iguales.

Antes de cada sustitución se hicieron backups SQLite consistentes y
`quick_check`, además de guardar configuración, inspección y registros en un
directorio privado `0700`, con archivos sensibles `0600`. El último está en:

```text
/opt/xmpp/backups/recovery-20261022T141142Z
```

Su `rollback.sh` restaura únicamente la imagen/configuración del puente a la
R1 anterior, sin restaurar ni sobrescribir datos y sin tocar otros servicios:

```sh
ssh rayoscompany sh /opt/xmpp/backups/recovery-20261022T141142Z/rollback.sh
```

La R1 y la base original siguen disponibles. El respaldo para volver desde R1
a la base original está en `/opt/xmpp/backups/recovery-20261022T140023Z`.
El watchdog corregido se conserva al revertir la imagen; el original se guardó
en `/opt/xmpp/backups/recovery-20261022T135700Z/slidge-watchdog`.

La primera sustitución R1 se revirtió automáticamente por una guardia demasiado
estricta con el orden de `Env`/montajes de Compose. Se verificó que los valores
eran iguales y se corrigió la comparación, no se desactivó la protección.
La reversión real quedó así ejercitada antes del despliegue final.

No se construyó un cliente Windows, no se publicó una release ni se cambió el
manifiesto estable del appliance. No fue necesario hacer commit ni push.

## Verificación productiva y límites

Una consulta autenticada con un recurso XMPP aislado y prioridad negativa
confirmó `connected`, autenticación WhatsApp, sincronización `ready`, contrato
de vinculación 1 y capacidad de voz. No inició QR ni envió mensajes de chat.

La observación pasiva final registró 13 muestras hasta las **08:26:53 UTC−06:00**,
con 15 minutos desde el arranque y tres activaciones del watchdog posteriores
al despliegue (08:16:38, 08:21:38 y 08:26:38). Permanecieron el mismo contenedor
y la misma imagen: cero reinicios, panic, bloqueos SQLite, errores LTHash y
`recovery_failed` en esa ventana. La consulta autenticada final volvió a
confirmar `connected`, `ready`, WhatsApp autenticado y capacidad de voz.

El arranque registró un error de vinculación `expired`, con dos secciones de
traceback, y tres callbacks lentos de QR; esos contadores dejaron de crecer
durante la observación y no provocaron reinicios. Esto no debe ocultarse ni
interpretarse como caída general de la cuenta que confirmó autenticación.
Como no reapareció LTHash, **no se observó una recuperación real desde el teléfono**:
la nueva ruta está comprobada por pruebas nativas, no por una respuesta
productiva de snapshot. La observación no garantiza estabilidad durante horas.

Pendiente confirmación humana de entrada/salida individual y grupal, foco/NVDA
y estabilidad de uso prolongado. Las correcciones locales del cliente, incluido
el rejoin MUC, sólo entran en uso al relanzar CAN desde el código actualizado.
No se reenviaron mensajes inciertos ni se borraron sesiones o conversaciones.

## Archivos de esta entrega

- `tools/patch_slidge_recovery.py`: transformaciones de las ocho rutas instaladas.
- `tools/Dockerfile.bridge-recovery`: baseline, Go/race, biblioteca y smokes.
- `tools/bridge_payload_recovery_test.go`: composición, contexto, recuperación y limpieza concurrente.
- `tools/smoke_bridge_recovery_runtime.py`: SQL real temporal, callbacks y watchdog.
- `tools/slidge-watchdog` y `.gitattributes`: política acotada y conservación de LF en Windows.
- `tests/test_bridge_recovery.py` y `tests/suites.json`: contrato del parche catalogado.
- `cliente_xmpp/xmpp/client.py` y `tests/test_whatsapp_linking.py`: recuperación MUC acotada, además de las correcciones locales previas.
- Los dos informes del 22 de octubre: evidencia, validación y reversión.

## Referencias

- [Recuperación oficial de app-state](https://pkg.go.dev/go.mau.fi/whatsmeow#BuildAppStateRecoveryRequest).
- [Manejo de desconexiones 503 de WhatsMeow](https://github.com/tulir/whatsmeow/blob/main/connectionevents.go).
- [SQLite WAL y sus condiciones de concurrencia](https://www.sqlite.org/wal.html).

## Seguimiento: lectura y desconexiones largas

La revisión posterior confirmó los dos pendientes y permitió reproducir una
cascada de bloqueos que no estaba cubierta por la liberación del lector MAM.
Las correcciones siguen siendo locales, sin gestores ni wrappers nuevos.

### Correcciones comprobadas

| Defecto | Cambio y prueba |
|---|---|
| Caída de WhatsApp mayor de 190 segundos abre vinculación | El `snapshot()` real convierte `connecting/disconnected` en `connection_error/connection_timeout`. La UI mantiene recuperación para ese motivo **sólo si antes estaba autenticada**. Una regresión recorre 0/189/191/600 segundos y termina tanto en recuperación como en logout real; el timeout de un login inicial no se disfraza de recuperación. Se inicializa `whatsapp_recovery_roster`. |
| El commit de un marcador bloquea asyncio | La ruta de contactos ejecutaba SQL síncrono sobre el bucle que debía cerrar otros lectores. El handler instalado, con SQLite temporal y un lector que se libera desde ese bucle, falla en R2. La candidata ejecuta únicamente esa transacción en `asyncio.to_thread`, capturando el PK antes de cruzar hilos y abriendo su propia sesión ORM. Usa el `NamedLockMixin` existente para serializar marcadores del mismo contacto, incluso durante cancelación. |
| Un commit fallido devuelve una transacción abierta al pool | En R2, un COMMIT bloqueado deja `dbapi_connection.in_transaction=True` después de cerrar la sesión; una conexión SQLite independiente sigue bloqueada aunque el lector original ya cerró. El hook `reset` del engine SQLite revierte esa transacción pendiente. El trabajador de marcadores usa además `session.begin()` para rollback en caso de error. La regresión verifica desde una conexión independiente que las filas siguen intactas y otra escritura puede completarse. |
| Marcador duplicado o desconocido consume mensajes posteriores | El pop original barre todos los pendientes cuando ya no encuentra el horizonte. La ruta corregida comprueba primero el horizonte del mismo contacto; si no existe, no consume nada. Se prueba duplicación, solapamiento, cancelación y conservación de otro contacto con el mismo ID. |

No se aumentan timeout, tamaño de pool ni reintentos; no se cambia WAL ni el
esquema. El SQL fallido sigue notificando error y no llama a `on_displayed`.
Los hooks de lectura grupal y `MARK_ALL_MESSAGES=False` conservan su ruta.
Esto impide una cascada reproducida; **no demuestra quién retenía cada lector
en todos los incidentes históricos ni elimina toda posibilidad de contención**.

La solución utiliza los mecanismos documentados de
[reset del pool](https://docs.sqlalchemy.org/en/20/core/pooling.html#custom-reset-on-return-schemes)
y [transacción con rollback](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#framing-out-a-begin-commit-rollback-block).
No se comparte una sesión ORM entre el bucle y el trabajador.

### Validación y candidata

- Python conexión/mensajería/historial/bridge: **550 métodos / 318 subcasos**,
  aprobados durante el cambio. Tras añadir la comprobación de validación atómica
  del parche, bridge final: **171 métodos / 102 subcasos**, aprobados.
- Módulo de vinculación: **40 métodos / 25 subcasos**, aprobado. La nueva prueba
  de caída larga rechazó primero el código anterior en sus dos desenlaces.
- Ruff completo y `git diff --check`: aprobados. El cierre nativo wx de la suite
  monolítica documentado antes no se declara corregido; no se repitió esa suite.
- `tools/Dockerfile.bridge-read-markers` construye una capa Python sobre R2,
  prueba el fallo original sin red, aplica el parche dos veces y ejecuta las
  regresiones instaladas de marcadores, recuperación, vinculación e historial
  MUC. Todos esos controles aislados pasaron, sin datos productivos.
- Candidata: `cliente-xmpp-bridge:read-markers-r3-20261022`,
  `sha256:532e4f8eced744f69f1188424d7637594676415bf5267adfb7addba0b7a53193`.
  Sólo cambian `slidge/db/meta.py` y
  `slidge/core/dispatcher/message/marker.py`: **1.654 archivos de paquetes
  idénticos**, incluida la biblioteca nativa. No se recompiló Go ni se
  actualizaron dependencias; la validación Go anterior no es una ejecución nueva.

### Ensayos productivos y reversión

Se autorizaron y realizaron dos sustituciones del puente. Antes de cada una se
guardaron cuatro backups SQLite consistentes con `quick_check` aprobado,
configuración y registros privados. Se conservaron variables, credenciales,
montajes, TLS, puertos, comandos, usuario y los demás contenedores. El watchdog
no se modificó: se usó su lock existente durante la intervención.

1. Primer ensayo: el probe autenticó contra XMPP, pero discovery agotó 10 s.
   No se aprobó la candidata y se restauró R2. La primera guardia no conservó el
   log del contenedor candidato antes de eliminarlo; esa limitación se corrigió
   para el segundo ensayo.
2. Segundo ensayo, arranque **21:26:31 UTC / 15:26:31 UTC−06:00**: una consulta
   con recurso único y prioridad negativa esperó el arranque sin enviar chats.
   WhatsApp autenticó a las 21:26:36; a las 21:27:32 fallaron `GetGroups` y
   `GetContacts` con `context deadline exceeded`. La excepción no recogida de
   sincronización llegó al handler fatal de Slidge; Docker registra salida
   **3** y reinicio a las 21:27:37. El probe recibió error IQ y no aprobó R3.
3. La guardia restauró **R2 a las 21:27:52 UTC / 15:27:52 UTC−06:00**.
   El probe posterior confirmó XMPP autenticado, WhatsApp `connected`,
   sincronización `ready` y voz disponible. No se dejó R3 activa a pesar de
   sus pruebas aisladas aprobadas. La causa del timeout de arranque y su relación
   con los dos cambios Python **siguen pendientes**; el código nativo es idéntico.

A las **21:32:56 UTC**, R2 llevaba 304 segundos activa sin reinicios, panic,
bloqueos SQL ni `recovery_failed` desde la reversión. La consulta autenticada
posterior volvió a confirmar `connected/ready` y voz. Eso no significa ausencia
de errores: se registró `can-link:expired` en una operación de login a las
21:30:37 y un `ElementTree.ParseError` al reconstruir historial MUC desde el
archivo a las 21:33:11. No se modificaron mensajes almacenados para ocultarlo;
el historial XML inválido requiere diagnóstico aparte.

Respaldos privados de estos ensayos:

```text
/opt/xmpp/backups/read-markers-r3-20261022T212353Z
/opt/xmpp/backups/read-markers-r3-20261022T212613Z
```

El segundo conserva `candidate-failure.log`, `candidate-inspect.json` y
`rollback.sh`. La reversión restaura sólo imagen/configuración, nunca bases
antiguas sobre mensajes nuevos. Perder el coordinador o vencer el plazo también
obliga a revertir. No se repitió una tercera sustitución sin nueva evidencia.

### Evidencia adicional y límites

- En R2 se revalidaron **18 líneas** `database is locked`, correspondientes a
  nueve excepciones registradas dos veces: actualización de contactos/grupos a
  las 16:52 UTC; marcadores y recepción de contactos a las 18:10–18:11 UTC.
  El sitio de una excepción de commit no identifica por sí solo al lector.
- La petición LTHash fue a las **14:37:56 UTC / 08:37:56 UTC−06:00**; no se
  encontró `stage=recovered`. Sigue sin confirmación de recuperación real desde
  el teléfono; no se borraron dispositivos ni se forzó otra petición.
- Prosody registra desconexión/autenticación del componente a las
  15:58:52/15:58:57 y 16:06:23/16:06:28 UTC. El agente revisor las atribuyó a
  reinicios manuales; esta sesión confirma esas horas y el arranque R2 de las
  16:06:24, **no quién ejecutó los comandos**. No hubo reinicios registrados por
  el watchdog desde la intervención inicial. La ventana original de estabilidad
  hasta las 08:26 no cubría estos hechos posteriores.
- Pendientes: resolver/aislar el fallo de arranque antes de aceptar R3, envío y
  recepción humanos individuales y grupales, uso prolongado y NVDA. La corrección
  local de la caída larga necesita relanzar CAN desde el código actualizado.
- Sin commits, push, build Windows, publicación GHCR, release ni cambios del
  manifiesto estable durante este seguimiento.

Archivos adicionales: `tools/patch_slidge_read_markers.py`,
`tools/smoke_bridge_read_markers_runtime.py`, `tools/Dockerfile.bridge-read-markers`,
`cliente_xmpp/ui/main_window.py` y regresiones en los dos módulos de pruebas
existentes. No se añadieron abstracciones de conexión ni servicios nuevos.

## R4: sincronización posterior a autenticación sin cierre fatal

El informe posterior permitió aislar el segundo ensayo: el error nativo
`GetGroups` sale de `Bookmarks.fill()` después de autenticar WhatsApp;
`monitored_login` lo relanza y el handler fatal de `BaseGateway` detiene el
bucle. La regresión instalada reproduce ese recorrido en R2/R3 y comprueba el
handler real, no sólo una excepción artificial de la UI. El límite nativo de
30 segundos también está confirmado en ambas imágenes.

Esto demuestra un defecto compartido por R2/R3, **no demuestra por qué
WhatsApp respondió tarde**. Que la biblioteca nativa sea idéntica tampoco
descarta todo efecto de temporización de los cambios Python. La hipótesis sobre
reinicios próximos y app-state sigue sin prueba causal.

Hay una corrección importante al informe adjunto: el log de roster completado a
las 21:25:14 está en `container.log` del respaldo del segundo ensayo, que captura
el R2 anterior a ese ensayo. El primer R3 ya había sido sustituido por R2 a las
21:24:29. Por tanto, ese log **no acredita que el primer R3 terminara sano**.
Sí había un criterio de discovery demasiado corto; faltan los logs eliminados
de aquella candidata para calificar todo el primer ensayo como falso negativo.

### Cambios acotados

- `tools/bridge_linking.py`: una excepción de sincronización con WhatsApp ya
  autenticado queda como `sync=error`, sin escapar al cierre fatal. Los errores
  anteriores a autenticar siguen propagándose.
- El `LinkingSession` existente reintenta sólo los `fill()` pendientes después
  de esperas de **30, 60 y 120 segundos**, sin llamar de nuevo a login. Una tarea
  evita reintentos duplicados y se cancela al desconectar, desvincular o cambiar
  de intento; una respuesta tardía no marca lista una sesión nueva. Tras éxito
  restablece la presencia genérica de sesión iniciada. Si agota los intentos,
  conserva `sync=error`, sin afirmar que terminó.
- `tools/patch_slidge_recovery.py`: los tres contextos de contactos, grupos y
  suscripciones pasan de **30 a 120 segundos**. La actualización del parche v1
  al v2 exige exactamente los tres anclajes y es idempotente. No cambia el
  timeout SQLite descrito en la sección anterior.
- `tools/Dockerfile.bridge-sync-recovery`: conserva la corrección SQL de R3,
  prueba que su wrapper instalado reproduce el cierre fatal y después aplica
  R4. Reconstruye Go con las mismas dependencias fijadas; no introduce un gestor
  de conexiones, servicio ni barrera de eventos.

### Pruebas R4

- Conexión/mensajería/historial/bridge: **559 métodos / 323 subcasos**, aprobados.
  Después del último ajuste de presencia se repitió sólo vinculación:
  **47 métodos / 30 subcasos**, aprobados.
- Las regresiones incluyen error posterior a autenticación en contactos y
  grupos, fallo previo a autenticación, agotamiento, cancelación, deduplicación,
  resultado obsoleto, readiness cancelado y resultado normal sin reintentos.
  El módulo del parche nativo pasó **6 métodos / 9 subcasos**.
- La imagen ejecuta Go normal y race: **83 métodos / 69 subcasos en cada
  ejecución**, cero fallos y los dos skips upstream conocidos (`TestSmplTablesCDF`
  y `TestSynth`). También ejecuta los smokes instalados de
  recuperación, lectura, vinculación e historial MUC, aislados sin red ni datos
  productivos. La prueba de `GetGroups` pasa de error a `ready` sin otro login
  y verifica que la llamada nativa no bloquea el bucle.
- Imagen construida:
  `cliente-xmpp-bridge:sync-recovery-r4-20261022`,
  `sha256:0cc505369fadbc43625db15023c4f3c852d7db8bb74e0f0582a6dc61c8ba5e9a`.
- Comparación de paquetes contra R2: **1.651 archivos idénticos de 1.656**,
  ninguno eliminado. Sólo cambian `meta.py`, `marker.py`, `linking.py`,
  `session.go` y la biblioteca nativa recompilada. La imagen final contiene
  exactamente los tres contextos nativos de 120 segundos.

### Guardia de despliegue R4

La intervención conserva cuatro respaldos SQLite consistentes y privados,
configuración original y rollback sin restaurar bases antiguas. Exige diez
minutos desde el arranque previo, espera hasta 90 segundos la marca de arranque
de Slidge y reintenta discovery/estado en vez de descartar por un único IQ de
diez segundos. Se aplica `stop_grace_period: 30s` únicamente al puente y también
se usa el plazo explícito de 30 segundos al sustituirlo. Un plazo mayor reduce
el riesgo de SIGKILL; no garantiza que cualquier cierre termine a tiempo.

Antes de aceptar exige `connected/ready`, al menos 600 segundos con la misma
identidad y arranque de contenedor, sin `Crash in`, panic, salida 3 ni reinicios,
y otra consulta final autenticada. Si falla la validación o se pierde el
coordinador, revierte a R2 y conserva el log candidato. Se mantiene el lock
existente del watchdog durante la intervención: sus ejecuciones concurrentes
se omiten, por lo que esa ventana no acredita ciclos efectivos del watchdog.
No se cambia su archivo ni su timer. Los scripts de esta intervención están en
`.test-results/bridge-r4-20261022/` (ignorados, no son una herramienta general
de despliegue).

La prueba humana individual/grupal, el XML inválido de historial y la
recuperación LTHash siguen siendo comprobaciones independientes. No se envían
mensajes a conversaciones reales desde las sondas.

### Resultado productivo R4

**R4 quedó activa y fue aceptada**, no R3. El contenedor
`88e0de7b376429d90a886f310f339b3e6ec85b4ef94b61aefb608eede40cd493`
arrancó el **22 de octubre a las 22:02:29 UTC / 16:02:29 UTC−06:00** con la
imagen `0cc505369f...` documentada arriba. Se verificaron las variables,
montajes, usuario, comandos, puertos, política de reinicio y restricciones
existentes; los demás contenedores conservaron su identidad.

- Probe inicial y final: XMPP y WhatsApp autenticados, `connected`, `ready`,
  contrato de vinculación v1 y capacidad de voz anunciada. No es una llamada
  de voz ni una prueba de mensajes.
- Observación de **22:04:38 a 22:14:38 UTC**: **600,2 segundos, 21 muestras**,
  misma identidad y `StartedAt`; **cero** `Crash in`, panic, salida 3 y
  reinicios Docker. También cero `database is locked`, `recovery_failed` y
  `context deadline exceeded` durante esa ventana.
- Sí hubo **dos tracebacks**: `xml.etree.ElementTree.ParseError` al reconstruir
  historial durante `join_muc`, y `can-link:expired` en otro intento de login
  a las 22:05:15. Ambos ya estaban documentados en R2. No tumbaron R4; eso
  **no significa que el grupo afectado funcione correctamente**.
- Verificación posterior a liberar el lock, **22:15:15 UTC**: R4 sigue
  ejecutándose, `RestartCount=0`, `StopTimeout=30`, timer del watchdog activo
  y archivo sin cambios. Su SHA-256 sigue siendo
  `bca737f368d4784a240740769658eaa0e98f891b8bbf73dd8354c0b1958b16c7`.
- No se recuperaron eventos Docker del contenedor R2 eliminado; no se afirma
  con esa evidencia que su cierre anterior haya terminado sin SIGKILL.

Respaldo privado con cuatro SQLite verificados, configuración, inspecciones y
rollback sin restauración de bases:

```text
/opt/xmpp/backups/sync-recovery-r4-20261022T220223Z
```

El contexto y la evidencia de aceptación quedan en
`/opt/xmpp/sync-recovery-r4-20261022-YKUJ4g/` (`build-result.json`,
`acceptance.json` y reportes nativos incluidos en la imagen).

**Pendiente de validación humana:** envío y recepción desde CAN tanto
individual como grupal. Se pidió al usuario; no se recibió confirmación en este
cierre y las sondas no enviaron chats. Para cargar el ajuste local de caída
larga hay que relanzar CAN desde este código. Siguen pendientes el diagnóstico
del historial XML inválido, LTHash sin confirmación de recuperación desde el
teléfono, el login expirado, el uso prolongado y la comprobación real con NVDA.
No se borraron mensajes ni sesiones para disimularlos.

### Inventario de cambios pendientes en Git

| Archivos | Alcance |
|---|---|
| `cliente_xmpp/ui/main_window.py` | Caída larga transitoria e inicialización del estado de recuperación. |
| `tools/bridge_linking.py` | Sincronización autenticada no fatal y reintentos acotados en el estado existente. |
| `tools/patch_slidge_recovery.py` | Actualización nativa v2 a 120 segundos. |
| `tools/patch_slidge_read_markers.py` | Transacción de marcadores fuera del bucle y rollback SQLite al devolver conexiones. |
| `tools/smoke_bridge_recovery_runtime.py`, `tools/smoke_bridge_read_markers_runtime.py` | Regresiones de los handlers realmente instalados, sin red ni datos reales. |
| `tools/Dockerfile.bridge-read-markers`, `tools/Dockerfile.bridge-sync-recovery` | Recetas reproducibles R3 y R4 con rechazo de baseline y controles aislados. |
| `tests/test_whatsapp_linking.py`, `tests/test_bridge_recovery.py` | Regresiones focalizadas dentro de módulos ya registrados en `tests/suites.json`. |
| `docs/PUENTE_RECUPERACION_2026-10-22.md` | Evidencia, resultados, reversión y límites de esta investigación. |

Sin commits, push, build Windows, tags, release ni publicación GHCR en esta
intervención. Se conserva el manifiesto estable y no se modifican servicios
ajenos al puente.

## CAN sin recepción: reintento XMPP bloqueado por un Future terminado

**Se encontró y corrigió un atasco reproducible en el cliente**, con una
modificación de cinco líneas, incluidos comentarios, en
`BridgeXmppClient._schedule_reconnect()`. No se añade ningún componente ni se
cambia producción. Es compatible con el incidente observado, pero no se afirma
que fuera su causa exacta: el proceso afectado ya se había cerrado al investigar
su estado interno.

### Evidencia y mecanismo

- El 22 de octubre, a las 19:58–20:03 UTC−06:00, CAN seguía abierto y respondía,
  pero sólo tenía sockets loopback, sin TCP XMPP remoto. Prosody había registrado
  su conexión a las 17:50 y su desconexión a las 19:10:56. El servidor respondió
  a una sonda anónima de stream/STARTTLS en 0,28 segundos. El usuario confirmó
  después que cerrar y abrir CAN recuperó el funcionamiento; no especificó una
  matriz de envío/recepción individual y grupal.
- En Slixmpp 1.16 instalado, perder el transporte durante STARTTLS puede dejar
  `_current_connection_attempt` apuntando a un Future **terminado con error**.
  `is_connecting()` sólo comprueba que la referencia no sea `None`:
  [implementación upstream](https://slixmpp.readthedocs.io/en/latest/_modules/slixmpp/xmlstream/xmlstream.html#XMLStream.is_connecting).
- La guardia anterior de CAN interpretaba ese resultado como un intento aún
  activo. Consumía su único timer de reconexión sin llamar a `connect()` ni
  programar otro. La reproducción dejó `connected=False`, `is_connecting=True`,
  `Future.done()=True` y cero llamadas a conectar después del timer.
- La guardia corregida sólo descarta el reintento si hay transporte conectado o
  un Future realmente **pendiente**. Conserva el host/perfil, la deduplicación y
  la prohibición de reconectar tras desconexión voluntaria o fallo de credenciales.

### Verificación y alcance

| Comprobación | Resultado |
|---|---|
| Regresiones antes de corregir | Siete fallos reproducidos: el reset TLS y los intentos terminados, cancelados o fallidos, con host explícito o discovery. |
| TCP/STARTTLS real en loopback | Un servidor ficticio anuncia STARTTLS y corta después del ClientHello real. La guardia de `HEAD` agota el plazo sin abrir otro TCP; la corregida abre una segunda conexión. Sin cuentas ni servidores reales. |
| Nuevo módulo `tests/test_xmpp_reconnection.py` | **6 métodos / 12 subcasos**, aprobados. Incluye no duplicar intentos pendientes/transporte vivo, ráfagas de desconexiones y no reconectar tras parada voluntaria o fallo de autenticación. |
| Conexión/mensajería/historial/bridge | **565 métodos / 335 subcasos**, aprobados, todas las capas de esas áreas. No es la suite monolítica completa ni una prueba WhatsApp E2E. |
| Ruff completo y `git diff --check` | Aprobados. |

El módulo está registrado en `tests/suites.json`, con la prueba TCP real en la
capa `integration`; las demás son `fast`. Esta unidad afecta sólo
`cliente_xmpp/xmpp/client.py`, ese módulo, su registro y esta documentación.
Puede revertirse independientemente de los arreglos previos del puente.

El CAN que el usuario abrió a las 20:04 precede al cambio: **la corrección se
cargará en la próxima apertura desde este código**. No se cerró el cliente,
reinició ningún servicio, construyó/publicó imagen, hizo commit ni push durante
esta corrección. La comprobación de estabilidad prolongada sigue pendiente;
tampoco se atribuyen a este defecto los bloqueos SQL grupales o cortes del
componente documentados en el seguimiento anterior.

## Auditoría extremo a extremo posterior a R4

**Resultado parcial, 22 de octubre de 2026, 20:29–20:43 UTC−06:00:** dos defectos
adicionales reproducidos y corregidos en el parche existente. No se construyó
ni desplegó otra imagen. No se declara resuelta la incidencia completa: siguen
pendientes la causa exacta de la contención SQL, aplicar las correcciones del
puente y la comprobación humana de mensajes/estabilidad.

### Recorrido y evidencia actual

| Capa | Comprobación y límite |
|---|---|
| Perfil del cliente | Configuración seleccionada `remote`, servidor XMPP por TCP 5222 con TLS. El perfil local se conserva independiente; no se preparó WSL ni se modificó su appliance. |
| Transporte Windows–Prosody | Stream anónimo y STARTTLS reales: certificado verificado, TLS 1.3, 0,595 s; certificado vigente hasta el 2 de diciembre. Sin SASL, QR ni chats. Es una muestra de conectividad, no prueba de ausencia de cortes anteriores. |
| SSH | Acceso administrativo correcto mediante el alias existente. El transporte de CAN es XMPP directo, no un túnel SSH. No se cambió configuración SSH ni se atribuye el incidente a keepalives de SSH. |
| Host | Uptime 57 días, disco 27 % y aproximadamente 10 GB de memoria disponible durante la muestra; contenedor sin OOM. No prueba ausencia de picos históricos. |
| Prosody | Contenedor activo desde el 3 de octubre, cero reinicios Docker; comparte `xmpp-net` con el puente. No se reinició ni cambió su configuración/TLS. |
| Puente | Sigue R4 `0cc505369f…`, mismo contenedor `88e0de7b…`, `RestartCount=1`, nuevo arranque 23 de octubre 02:21:44 UTC / 22 de octubre 20:21:44 local. |
| Dependencias instaladas | Slidge `0.4.1.dev35+g0f0f993f0`, slidge-whatsapp `0.4.0b1.post7.dev0+25431c4`, Slixmpp servidor `1.17.0`, SQLAlchemy `2.0.51`. No se actualizaron dependencias. El cliente usa su entorno Conda, no estas librerías del servidor. |
| Sesión WhatsApp | Desde el último arranque hay un login exitoso y roster completado; cero líneas SQL lock, panic, timeout nativo o cierre fatal en la ventana posterior consultada. Sí hay tres líneas ParseError y una `can-link:expired`. No significa mensajería E2E aprobada ni estabilidad prolongada. |
| SQLite | Base activa Slidge en `journal_mode=delete`; message-info y transcripción usan WAL. La muestra de descriptores encuentra un único proceso del puente con la base activa abierta. No se cambió journal, timeout ni pool; falta identificar al lector/transacción concreto del bloqueo anterior. |
| Watchdog | Timer activo; hash del script idéntico al verificado tras R4 (`bca737f3…`). No se reactivaron reinicios por advertencias ni se cambió el presupuesto. No hay healthcheck de aplicación configurado: contenedor `running` no acredita salud del puente. |
| Reconexión y UI | Se revisaron la guardia del Future terminado, generaciones, sondeo, estados transitorios y preservación de caché/foco. El ping XMPP existente usa intervalo 60 s y timeout 45 s; no se añadió un segundo monitor. Las regresiones pasan; no se certifica NVDA ni cada incidente histórico. |

Durante la auditoría CAN apareció abierto a las 20:29:23. En la muestra de las
20:43 ya no había procesos `cliente-xmpp`/`python` ni TCP 5222 visibles. Las
herramientas de esta intervención no cerraron el cliente; esa última muestra
no demuestra otro atasco de reconexión.

### Cierre fatal: corregir la atribución, no esconder errores

El stack fatal de **02:21:24 UTC / 20:21:24 local** es:

```text
handler_callback_routine
  → MucMiscMixin.__on_group_chat_error
  → get_muc_from_stanza → _get_session
  → slidge.core.dispatcher.util.Ignore
```

Los bloqueos SQLite de participantes/archivo preceden al cierre, y otro bloqueo
impide terminar limpiamente `logout`; son fallos reales. Sin embargo, **la
excepción que dispara el handler fatal es `Ignore`, no el SQL del participante**.
La proximidad temporal no basta para atribuirle a ese SQL la caída del proceso.

Slidge descarta ecos de su propio componente lanzando `Ignore`, que hereda de
`BaseException`. El handler de errores grupales sólo capturaba `XMPPError`.
La corrección añade `Ignore` a ese `except` específico y a su import; conserva
la política global de errores. No captura indiscriminadamente `BaseException`,
no reintenta mensajes ni convierte errores de programa en éxito.

### Historial XML: carácter inválido confirmado

Una consulta de sólo lectura a las **5.000 entradas más recientes**, sin exportar
cuerpos, encontró una entrada con ParseError y un `U+000B` literal. La conexión
se cierra antes del análisis XML. Es una muestra acotada, no un escaneo de todo
el archivo ni prueba de que no existan otros formatos inválidos.

`HistoryMessage` sustituye únicamente caracteres prohibidos por XML 1.0 por
`U+FFFD`: al reconstruir cadenas antiguas y al preparar texto, colas y atributos
de nuevas entradas. **No reescribe ni borra las filas anteriores.** Conserva
IDs, namespaces, Unicode válido y espacios XML permitidos. Los errores
estructurales de XML siguen lanzando ParseError, sin saltar mensajes en silencio.
El rango procede de [XML 1.0, caracteres](https://www.w3.org/TR/xml/#charsets).

### Validación y unidad de cambio

- Se amplió `tools/patch_slidge_recovery.py`, no se creó otro servicio ni gestor.
  El patcher valida todas las anclas antes de escribir y sigue siendo idempotente.
- En un contenedor **desechable**, creado desde la R4 instalada, sin red ni
  montajes productivos: las fuentes originales reproducen el fatal `Ignore`
  y el ParseError. Tras aplicar el parche a ese filesystem efímero, ambos pasan.
  Las pruebas se ejecutan con el usuario `slidge`, no sólo como root.
- También pasan los smokes instalados de recuperación completa, marcadores de
  lectura, vinculación e historial MUC (20 grupos / 1.000 mensajes). Se conservan
  los controles de liberación SQL, cancelación y sincronización autenticada.
  El smoke de vinculación lee reportes Go incorporados en la imagen: **no se
  ejecutó Go/race nuevamente ni se recompiló la biblioteca nativa**.
- La matriz XML cubre 36 caracteres, lectura antigua, nuevas entradas, IDs y
  propagación de errores estructurales. La prueba MUC conserva el retiro del
  recurso humano correcto y el tratamiento de recurso ausente/XMPPError;
  ValueError y cancelación siguen propagándose.
- Suite local de todas las capas de bridge/conexión/mensajería/historial:
  **567 métodos / 343 subcasos**, 41,643 s, cero fallos/errores/omisiones.
  Registro ignorado: `.test-results/20261023T024111019303Z-all.json`.
  No es la suite monolítica completa; el cierre nativo wx previamente documentado
  no se declara corregido. Ruff completo y `git diff --check`, aprobados.

Archivos de esta unidad: `tools/patch_slidge_recovery.py`,
`tools/smoke_bridge_recovery_runtime.py`, `tests/test_bridge_recovery.py` y este
informe. Para revertir sólo esta unidad se retiran los dos transforms nuevos,
su registro y sus regresiones, preservando R4 y los cambios ajenos anteriores.

### Pendientes explícitos

1. Autorizar y ejecutar una imagen incremental/despliegue del puente con respaldo
   consistente y rollback. La R4 productiva aún no contiene estos dos cambios.
2. Aislar el propietario de la transacción/cursor en el próximo bloqueo SQL;
   no cambiar WAL/pool/tiempos a ciegas. La [documentación de SQLite sobre
   bloqueos](https://www.sqlite.org/lockingv3.html) explica la contención entre
   lectores y escritores, pero no identifica al causante de este caso.
3. Confirmar la recuperación LTHash y aclarar el login expirado con evidencia
   adicional, sin desvincular ni emitir otra solicitud al teléfono por ensayo.
4. Después de aplicar: envío y recepción humanos individuales/grupales,
   reconexión tras caída y uso prolongado; accesibilidad real con NVDA aparte.

Sin build de Windows ni imágenes, commit, push, tag, publicación, reinicios
productivos, envíos de chat, QR, logout ni borrado de sesiones/datos en esta
intervención. Se preservaron los catorce archivos ya pendientes al comenzar.

## R5 aplicada en producción

Con autorización explícita del usuario se construyó y sustituyó **únicamente
el puente**, conservando sesiones, datos, configuración, capacidades de voz y los
demás servicios. R5 contiene los dos cambios Python descritos arriba, no nuevos
timeouts, WAL, gestores de conexión ni actualizaciones de dependencias.

### Imagen ejecutable y conservación de la base

La R4 original tiene **127 capas**. La primera construcción multicapa falló con
`max depth exceeded`; una segunda de una sola capa alcanzó a construirse, pero
Docker tampoco pudo ejecutarla. **Ninguna se desplegó.** El control de arranque
de la candidata impidió confundir un build exitoso con un artefacto utilizable.

Se compactó el filesystem de un contenedor **desechable de la imagen R4**,
sin cuentas ni montajes productivos, conservando íntegramente `User`, `Env`,
`Entrypoint`, `WorkingDir` y `Labels`. La imagen no declara volúmenes. La copia
de una capa se comparó contra R4: **14.863 entradas**, sin cambios, agregados ni
borrados de contenido, permisos, propietario/grupo o destino de enlaces, fuera
de los montajes que Docker genera en runtime (`/dev`, `/proc`, `/sys`, `/run`,
temporales y archivos de hostname/hosts/resolver).

- Base compactada: `cliente-xmpp-bridge:r4-flat-base-20261022`,
  `sha256:abd8dcf42967d692d24fcb404b2a2b7cddfcbc2d4864bfcf2a02a9a61052a4c6`.
- Candidata final: `cliente-xmpp-bridge:muc-xml-r5-flat-20261022`,
  **`sha256:0bb0d19f468de21e424a1aac545423fb65282e40f3121ab9564c454255c129a2`**,
  dos capas y ejecutable comprobado.
- Comparación de paquetes: **1.758 archivos**, 1.756 idénticos, dos cambiados y
  ninguno agregado/eliminado. Esta enumeración incluye cachés Python, por lo que
  no se compara directamente con los totales históricos de otras enumeraciones.
  Sólo difieren `slidge/core/dispatcher/muc/misc.py` y
  `slidge/util/archive_msg.py`; la biblioteca nativa permanece idéntica.
- Configuración de imagen idéntica salvo la nueva etiqueta descriptiva de R5.
  No se recompiló Go ni se ejecutó nuevamente race; los reportes incorporados
  que consulta el smoke siguen siendo evidencia histórica.

La receta reproducible es `tools/Dockerfile.bridge-muc-xml`. Requiere la base
compactada y verificada: **no añadir capas directamente a la R4 de 127 capas**.
Usa un único `RUN` offline con entradas de build mediante bind de sólo lectura;
rechaza ambos baselines, aplica el parche dos veces y ejecuta las regresiones
instaladas con UID `slidge`. El contexto excluye tar de exportación, registros y
scripts auxiliares. El contrato de la receta pasó con el módulo de nueve
métodos / 17 subcasos; Ruff completo y `git diff --check`, aprobados.

### Despliegue y aceptación

Antes de sustituir se guardaron cuatro backups SQLite consistentes con
`quick_check=ok`, Compose, inspecciones y registros privados en:

```text
/opt/xmpp/backups/muc-xml-r5-20261023T025835Z
```

Su `rollback.sh`, comprobado sintácticamente, vuelve a **R4**, nunca restaura
bases antiguas sobre mensajes nuevos. No fue necesario usarlo. Se reutilizó
la guardia anterior y el lock existente del watchdog, sin alterar su archivo,
timer, cooldown ni presupuesto. Perder el coordinador, exceder el plazo o fallar
la aceptación exige conservar diagnóstico y revertir la imagen/configuración.

| Evidencia | Resultado |
|---|---|
| Contenedor nuevo | `b9531396c93f1798c230ba0fadc6999183029db32ed2ace06581c202d8c58e8e` |
| Arranque | 23 de octubre **02:58:41 UTC** / 22 de octubre **20:58:41 UTC−06:00** |
| Probe inicial y final | XMPP autenticado, WhatsApp autenticado, `connected`, `ready`, contrato v1, capacidad de voz anunciada. No envía chats ni inicia QR. |
| Aceptación | **03:00:44–03:10:44 UTC / 21:00:44–21:10:44 local**, 600,1 s, 21 muestras, misma identidad y `StartedAt`. |
| Errores y reinicios en esa ventana | Cero reinicios, `Crash in`, panic, salida 3, `database is locked`, ParseError, `recovery_failed` y timeout nativo. |
| Configuración y servicios ajenos | Comando, entrypoint, usuario, entorno, montajes, red, puertos, restricciones y política de reinicio conservados. Los otros contenedores mantienen su identidad. |
| Comprobación posterior, 03:12:01 UTC | R5 sigue activa, `RestartCount=0`, `StopTimeout=30`, timer del watchdog activo; hash de su script sin cambios (`bca737f3…`). |

**Sí hubo un aviso:** a las 03:01:27 UTC aparece un traceback de
`can-link:expired`. Se comparó, sin mostrar ni guardar identidades, el hash del
JID del perfil remoto seleccionado contra las cuentas de los logs: el login
exitoso corresponde a ese perfil; el login con problema corresponde a **otra
cuenta registrada**. Por tanto, no se atribuye el aviso al CAN comprobado, pero
tampoco se declara ausencia global de errores. No se modificó ni borró la
cuenta que expira.

Contexto y evidencia privados: `/opt/xmpp/muc-xml-r5-20261022-qhn3he/`
(`build-result.json`, `flat-filesystem-verification.json`, `acceptance.json`).
La coordinación local está bajo `.test-results/bridge-r5-20261022/`, ignorado.
Se conservaron R4/R2 y los respaldos de reversión. No hubo commit, push,
build Windows, tag/release ni publicación GHCR.

### Lo que esta aceptación no demuestra

- **Mensajería humana individual/grupal y estabilidad prolongada:** pendientes
  de confirmar desde CAN. `connected/ready` no prueba entrega ni recuperación
  de todas las conversaciones. NVDA y una llamada real son pruebas aparte.
- **Causa del bloqueo SQL histórico:** sigue sin estar aislada. Un ensayo
  adicional sobre SQLite real y métodos instalados de R4 comprobó que
  `is_displayed_by_user`, `get_first/get_last` y consumir parcialmente el
  generador actual de archivo no bloquean por sí solos un escritor independiente.
  El generador parcial retiene un checkout, pero eso **no acredita un lock
  SQLite**. Se descartó esa explicación como causa suficiente; no se hizo un
  parche especulativo ni se cambió el journal/pool/timeout.
- El teléfono todavía debe acreditar la recuperación LTHash; la cuenta ajena
  que expira requiere su propio diagnóstico. No se emitieron peticiones de
  recuperación, desvinculación ni mensajes de prueba por ensayo.

Las correcciones locales de reconexión del cliente ya existentes se cargan al
abrir CAN desde el código actualizado; este despliegue no construyó ni instaló
un ejecutable Windows nuevo.

### Captura pasiva posterior

Hasta el **22 de octubre, 21:19:53 UTC−06:00**, R5 conserva su identidad y cero
reinicios, sin nuevos errores SQL, XML, panic ni cierres fatales desde el arranque
(aproximadamente 21 minutos). El aviso de cuenta ajena sigue siendo uno.

Una captura de **180,1 s / 683 muestras**, con intervalo nominal de 250 ms, de
los locks de kernel del archivo SQLite activo no observó locks en sus instantes
de muestreo. El proceso de captura terminó normalmente; no queda un monitor
programado o en segundo plano. Evidencia privada:
`passive-sql-lock-sample.json` dentro del contexto remoto de R5.

Esto **no prueba ausencia de transacciones cortas ni identifica al propietario
del bloqueo histórico**: el muestreo puede omitir operaciones breves y los locks
de kernel no distinguen todos los cursores/conexiones SQLite del mismo proceso.
No se provocó tráfico de WhatsApp ni se alteró el journal para forzar un fallo.
Sin una recurrencia o la prueba humana desde CAN, no hay evidencia para otro
parche causal y no se da por concluida la auditoría.

### Recaída con R5 sin reinicio

El usuario informó recepción real de mensajes durante unos tres minutos y
después «Reconectando con WhatsApp». Eso acredita recepción parcial, no una
matriz de envío/recepción individual y grupal ni estabilidad sostenida.

- R5 conserva imagen, contenedor y arranque, `RestartCount=0`, sin OOM,
  panic, cierre fatal ni ParseError en la ventana del incidente.
- A las **03:25:03 UTC / 21:25:03 local** hay dos líneas de una misma excepción
  `database is locked` en `on_marker_displayed → LegacyMUC.echo →
  MessageArchive.add → Session.commit`. Es la ruta grupal de archivado del eco,
  no el `pop_sent` individual que se movió fuera del bucle en R3/R4.
- La primera captura local, 21:27:38, ya no encontró el proceso anterior. A las
  21:28:08 apareció un nuevo CAN/Python; a las 21:29 estaba respondiendo y con
  TCP XMPP establecido. No se cerró ni reinició CAN desde las herramientas.
  No se pudo inspeccionar el estado interno del proceso que dio el primer aviso.
- Se comprobó mediante los descriptores del proceso real que el archivo de la
  captura pasiva anterior era el correcto: `/var/lib/slidge/slidge.sqlite`,
  mismo dispositivo/inode que `/opt/xmpp/slidge/slidge.sqlite`. No había locks
  en la muestra posterior; no se presenta esa ausencia como refutación del error.

**Reproducción del bloqueo del bucle:** en R5 instalada, dentro de un contenedor
desechable sin red ni montajes productivos, una SQLite real temporal mantiene
un lector explícito y programa cerrarlo en el bucle a los 25 ms. El eco grupal
original entra en COMMIT síncrono: tras 211 ms falla con `database is locked`,
el callback de liberación no pudo ejecutarse y no se envió ningún eco. La sesión
SQL se liberó al fallar. No se utilizaron cuentas ni mensajes reales.

Esto demuestra que el archivado de ese eco puede bloquear el mismo bucle que
debe atender red/control y liberar lectores. **No identifica todavía al lector
productivo ni prueba que cada aviso de reconexión tenga esa única causa.** Una
corrección no debe descartar marcadores ni trasladar objetos ORM mutables de
room/participant a otro hilo mediante un `to_thread` indiscriminado.

En este seguimiento no se modificó, reconstruyó ni reinició ningún servicio,
ni se abrió una sonda autenticada que pudiera alterar la recuperación observada.
Falta confirmar el estado actual del CAN relanzado para capturar otra recaída
antes de cerrarlo.

### Corrección del falso aviso en un CAN que sigue conectado

El usuario confirmó que el CAN actual seguía mostrando el aviso. A las 21:36:45
el mismo proceso Python iniciado a las 21:28:08 respondía y conservaba TCP XMPP.
Después de esas capturas se ejecutó una consulta autenticada de sólo estado,
con recurso único y prioridad negativa, sin QR, logout ni mensajes: el puente
respondió autenticación XMPP y WhatsApp positivas, `connected`, `ready` y
capacidad de voz. Esa consulta independiente no prueba la entrega de mensajes
ni revela por sí sola el estado interno del poller del cliente abierto.

Se encontró un error directo en `MainWindow._handle_whatsapp_link_state`:
en la rama que preserva conversaciones/foco durante actualizaciones autenticadas,
el texto sólo decía «WhatsApp conectado» si `recovered` era verdadero. Cuando
la sesión **ya estaba autenticada**, cambiar `sync` a `syncing`, `ready` o `error`
entraba en la misma rama pero escogía «Reconectando con WhatsApp». La clave del
estado podía quedar sin cambios después, conservando el aviso incorrecto.

La condición ahora usa **`event.authenticated`**, no sólo «acaba de recuperarse»,
para elegir el texto. No cambia permisos de envío, generación/intento, filtros
de cuentas, deduplicación, estado remoto, reconexión ni sincronización. Una
desconexión real sigue mostrando «Reconectando» y revocando acciones remotas.

- La nueva regresión de los tres estados autenticados falla **3/3** con la
  condición anterior y pasa con la corregida. También comprueba cabecera y barra
  de estado, foco intacto, conversación no sustituida y acciones habilitadas.
- Todas las capas del área `connection`: **122 métodos / 65 subcasos**, aprobados.
  Registro ignorado: `.test-results/20261023T033840549714Z-all.json`.
  Ruff completo y `git diff --check`, aprobados.
- El import del entorno Conda verifica que el launcher usa este checkout.
  El proceso ya abierto conserva el código anterior; la corrección se carga
  en la próxima apertura desde fuente. No se cerró el proceso por herramientas,
  construyó Windows ni reinició/desplegó otra vez el puente.

Este arreglo no pretende reparar el COMMIT bloqueante del eco de lectura
grupal ni se presenta como solución definitiva de recepción. Es una corrección
independiente, de una condición, que elimina un aviso falso demostrado sin
ocultar desconexiones reales.

### R6: COMMIT del eco grupal fuera del bucle de conexión

La autorización posterior del usuario incluye auditar, corregir y aplicar al
puente de producción. La alternativa de commit/push es condicional a no poder
resolverlo; no se hizo commit/push ni se construyó un binario Windows.

**Evidencia actual antes de cambiar producción:** a las 04:06 UTC, R5 mantenía
el contenedor/arranque original, sin reinicios ni OOM. Desde las 03:25 UTC había
dos líneas de una excepción SQL, con el mismo stack de marcador grupal,
y cero nuevos panic, Crash, ParseError, deadline o recovery_failed. Prosody no
había reiniciado y `slidge-watchdog.timer` seguía activo. Los hashes de
`slidge/group/archive.py` y `slidge/group/room.py` en vivo coincidían con las
fuentes auditadas. No se atribuye el bloqueo a SSH: el cliente usa XMPP/TLS
directo; la SSH es la vía administrativa.

#### Cambio acotado

- `MessageArchive` comparte la preparación del mensaje entre `add()` y la nueva
  `add_async()`. La ruta síncrona conserva sus callers y su transacción.
- La ruta asíncrona prepara la copia y sus metadatos de participante en el hilo
  propietario. Sólo XML serializado, fecha, identificador y claves escalares
  pasan al worker. Éste crea/cierra su propia sesión SQL; ningún Room,
  Participant ni Session ORM del hilo propietario cruza de hilo.
- `LegacyMUC.echo()` espera el commit antes de enviar. Reutiliza el lock por
  nombre ya existente en la sesión para mantener el orden por sala durante el
  nuevo `await`; la cancelación espera el worker antes de liberar el lock.
- Los envíos XMPP permanecen en el bucle propietario. Un fallo SQL sigue
  propagándose sin enviar un eco falsamente persistido. No se añaden reintentos,
  gestores, dependencias, cambios de journal/pool/timeouts ni borrados.

Esto sigue el modelo de [sesión por hilo de SQLAlchemy](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#is-the-session-thread-safe-is-asyncsession-safe-to-share-in-concurrent-tasks).

#### Pruebas antes del despliegue

| Comprobación | Resultado |
|---|---|
| R5 instalada en contenedor aislado, SQLite temporal real y lector liberable a 25 ms | Falla el commit, no corre el callback del bucle, cero ecos: baseline reproducida. |
| Misma reproducción con parche | El lector se libera desde el bucle; commit correcto y dos ecos posteriores, uno por recurso XMPP ficticio. |
| Contención persistente | Propaga OperationalError, no hay fila ni eco falsos; tras liberar el lector, la siguiente operación funciona. |
| Cancelación con worker en curso y segundo eco solapado | Mantiene exclusión hasta terminar SQL; la operación cancelada no envía ecos; las siguientes conservan orden. |
| Semántica de archivo | Equivalencia sync/async en XML, fecha y metadatos; deduplicación por sala/ID, resolución de PK no cacheada, no-store y reemplazos preservados. |
| Contratos locales nuevos | Cinco métodos y ocho subcasos aprobados; rechazan fuentes desconocidas/duplicadas y validan ambos archivos antes de escribir. |
| Áreas Python `bridge` y `connection`, todas sus capas | 302 métodos / 183 subcasos, 31.470 s, cero fallos/errores/omitidos. |
| Ruff global / `git diff --check` | Aprobados. |
| Imagen, sin red y con UID de `slidge` | Smoke nuevo y smokes existentes de markers, recovery, linking y MUC history aprobados. Los informes Go/race embebidos no cuentan como una nueva ejecución nativa. |

El test usa JID `.invalid`, datos temporales, sockets XMPP nunca conectados
y ningún montaje de producción. El worker de SQL y los envíos se comprueban
en hilos distintos; se verifica que las sesiones se devuelven al pool.

El primer intento de build no resolvió un ID local `sha256:` como `FROM`;
se cambió al tag local después de verificar su ID. Los fallos posteriores
detectaron dos problemas del fixture (horizonte distinto y plugin de no-store ausente),
corregidos antes de obtener la candidata. Ninguno modificó el servicio vivo.

#### Imagen y límite de reversión

- Receta: `tools/Dockerfile.bridge-group-echo`.
- Parche: `tools/patch_slidge_group_echo.py`; smoke:
  `tools/smoke_bridge_group_echo_runtime.py`.
- Imagen: `cliente-xmpp-bridge:group-echo-r6-20261022`.
- ID: `sha256:a772d3842cadcc1f72a85379b391e80190f5ddf6aebb44fae8e00d11babd51a8`.
- Base R5 verificada: `sha256:0bb0d19f468de21e424a1aac545423fb65282e40f3121ab9564c454255c129a2`.
- Tres capas. De 1 758 archivos de paquetes, 1 756 idénticos y sólo dos
  modificados: `slidge/group/archive.py` y `slidge/group/room.py`. Ningún archivo
  añadido/eliminado de paquetes ni cambio en biblioteca nativa/dependencias.
  Configuración de imagen idéntica salvo la etiqueta de esta corrección.
- Evidencia privada remota: `/opt/xmpp/group-echo-r6-20261022-ATvCTk/`.
- Respaldo: `/opt/xmpp/backups/group-echo-r6-20261023T040933Z/`; las cuatro bases
  se copiaron mediante SQLite backup y sus copias pasaron `quick_check`.
  Compose, configuración/inspect, watchdog y logs permanecen privados.
- `rollback.sh` revierte imagen/compose a R5, **no restaura bases antiguas sobre
  mensajes nuevos**. Sólo se recrea `slidge-whatsapp`; la guardia verifica
  identidad de otros contenedores, configuración protegida y hash del watchdog.

**Aceptación productiva aprobada:** la guardia cubrió 04:11:43–04:21:43 UTC
(22:11:43–22:21:43 local), **600.1 segundos / 21 muestras**, conservando
contenedor y arranque. Cero reinicios, OOM, `database is locked`, panic,
`Crash in`, ParseError, recovery_failed, deadline y eventos de salida con código 3.
Las sondas inicial/final autenticaron XMPP y WhatsApp, `connected/ready`,
contrato linking 1 y capacidad de voz. No enviaron mensajes ni operaciones
de QR/cancel/logout. El coordinador terminó con código 0 y `DEPLOYMENT_APPROVED`;
no queda un despliegue esperando aprobación ni debe ejecutarse de nuevo.

- Contenedor: `8475a47fed422f32d8a846b3cb25a5930a7b068eeb3018d2c1bf368f6d690c58`.
- Arranque UTC: `2026-10-23T04:09:38.813509364Z`.
- Configuración protegida, identidades de otros contenedores y hash del watchdog
  iguales a antes; sólo se recreó el puente. El rollback manual sigue disponible.
- Registro: `acceptance.json` en el directorio privado remoto; sondas resumidas
  y orquestación en `.test-results/bridge-r6-20261022/` (ignorado por Git).

`connected/ready` y una ventana sin errores **no acreditan entrega E2E ni
estabilidad indefinida**. El 23 de octubre el usuario confirmó envío/recepción
desde CAN después de dejarlo activo durante horas. Es la confirmación humana
de uso sostenido que faltaba, no una prueba automatizada ni una matriz explícita
individual/grupal, de llamadas o de foco/NVDA.
El cierre nativo de la suite wx monolítica sigue siendo una limitación separada
ya documentada; no se volvió a ejecutar ni se declara reparado por este parche.

Durante esta guardia apareció un `Traceback/can-link:expired`. Se verificó de
nuevo su atribución, sin confiar en la observación equivalente de R5: el hash
del bare JID del perfil remoto de CAN coincide con un login exitoso y con
**ningún** `Login problem`; el único login problem pertenece a otra cuenta
registrada. La sonda de CAN está autenticada. No se borró ni cambió esa cuenta
ajena; el diagnóstico guardado sólo contiene booleanos y conteos, no identidades.
