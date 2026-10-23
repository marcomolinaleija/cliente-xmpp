# Reconexión y vinculación: correcciones locales y riesgos del puente

> Este informe conserva la fase inicial de sólo lectura del servidor.
> La intervención posterior autorizada, imagen y despliegue están en
> [Recuperación del puente](PUENTE_RECUPERACION_2026-10-22.md).

Se corrigieron tres rutas reproducibles del cliente sin cambiar su arquitectura:
estados transitorios que abrían vinculación, generaciones invalidadas desde el
reloj de la UI y sondeo que no sobrevivía a su primer fallo. Producción se
inspeccionó exclusivamente en lectura. **La validación completa no está verde**:
persisten un cierre nativo de wx y un fallo temporal en una prueba de voz.

## Punto de partida y alcance

- Rama `vinculación`, HEAD `02913dff5a133af21f49aa0eab2ba656ece4629c`.
- Árbol e índice inicialmente limpios; no había ediciones de Claude pendientes.
  Se aprovechó su investigación y el trabajo ya incluido en ese commit.
- Revisados flujo de sesión, reconexión, eventos, permisos de envío, roster,
  sincronización inicial, vinculación, unión/pertenencia MUC y errores de mensajes.
- Sin gestores de conexión, wrappers de servicio ni capas nuevas. No se modificó
  el ciclo de vida de `XmppService`, el almacenamiento ni los contratos remotos.
- No se ejecutaron builds, commits, push, despliegues, reinicios, vinculación,
  logout, envíos de mensajes ni modificaciones de datos productivos.

## Causas confirmadas y correcciones

| Causa | Evidencia y cambio localizado |
|---|---|
| Un transporte temporalmente desconectado parecía desvinculación | `bridge_linking.py` publica `connecting/disconnected` con autenticación falsa. La UI la revocaba antes de su atajo heredado y abría vinculación. Ahora conserva conversación y foco si venía conectada, pero bloquea acciones remotas hasta autenticación positiva. Un logout real sigue abriendo vinculación. |
| La UI podía rechazar toda la sesión nueva | Al procesar `XmppDisconnected`, usaba `monotonic_ns()` del hilo UI. Si éste se retrasaba hasta después de la reconexión, su límite superaba la nueva generación. El evento existente lleva ahora la generación del protocolo cerrado, con valor predeterminado compatible; la UI invalida esa generación, ignora desconexiones anteriores y limpia autoridad/clave de estado. |
| Un fallo inicial dejaba de observar la recuperación | Discovery fallido devolvía `False`, entrando al flujo antiguo; `probe()` creaba el sondeo sólo tras un IQ exitoso. Ahora sólo un discovery exitoso sin la feature elige legacy. Los fallos mantienen el sondeo existente, con esperas de 3, 6, 12, 24 y hasta 30 segundos; el intervalo vuelve a 3 al recuperarse. |

El sondeo verifica que la sesión siga vigente antes de enviar y de publicar un
error sintético. Las cuatro acciones manuales conservan su manejo de error sin
caer a comandos antiguos por un timeout. No se reenvían mensajes inciertos.
Una recuperación no vuelve a aplicar el roster anterior ni roba foco por un
cambio de fase de sincronización; un roster realmente recibido durante la caída
sí se aplica al recuperar autenticación.

Cinco regresiones se contrastaron también con las funciones originales de HEAD,
cargadas sólo en memoria de un proceso de pruebas: rechazaron el código anterior
por transitorio, generación, primer probe, discovery y error tardío/backoff.
No se restauraron ni sobrescribieron archivos para efectuar esa comparación.

## Servidor: hechos, no modificaciones

Consulta SSH del 22 de octubre, aproximadamente 07:11–07:27, UTC−06:00.
Contenedor `slidge-whatsapp`, imagen `cliente-xmpp-bridge:inbound-pool-20261020`,
ID `sha256:1120cc79e8413fa455da3e84ac4991b83a4f03cf1326bd4ddf495044d415c182`.

- **Watchdog:** ocho reinicios el 21 de octubre entre 17:37 y 22:13, y otro el
  22 a las 06:56; no nueve la noche anterior. El timer corre cada cinco minutos.
  Su script `/usr/local/sbin/slidge-watchdog` suma `recovery_failed`, LTHash,
  panic, `503 stream error` y SQLite como señales severas. Reinicia con al menos
  tres señales y sin un login reciente, sin comprobar salud real.
- **Correlación:** siete de las nueve ventanas de quince minutos anteriores a
  esos reinicios contienen exclusivamente `recovery_failed` entre las señales
  contadas. Dos ventanas añaden errores SQLite. Ninguna contiene panic, 503 ni
  LTHash. Por tanto, las advertencias de lectura bastan para disparar reinicios;
  no se demostró que la mensajería estuviera caída en cada uno de esos momentos.
- **`recovery_failed`:** el código local de recuperación lo emite si falla
  `FetchAppState` al intentar reparar el hash de una colección para sincronizar
  lectura. Es un error real de esa operación, no prueba de muerte del proceso
  ni de pérdida de vinculación. No debe calificarse simplemente de inocuo.
- **Panic real:** tres nil-pointer panic en 36 horas, el 21 a las 10:35:19,
  16:06:37 y 16:10:38 (UTC−06:00), en `getMessagePayload`,
  `/integration/slidge_whatsapp/session.go:723`, desde `SendMessage`.
  La fuente instalada inicializa submensajes sólo si `payload == nil`; si existe
  otro tipo de payload, las rutas de ubicación, menciones o preview pueden
  desreferenciar un submensaje nulo. Falta reproducir el mensaje disparador
  exacto con fixtures y la biblioteca nativa; no se reparó ni recompiló el puente.
- **SQLite/LTHash:** el intervalo consultado contiene 26 líneas `database is
  locked` (no necesariamente 26 incidentes distintos), tres errores de LTHash
  y 110 advertencias `recovery_failed`. Hay trazas en historial MAM y marcadores
  de lectura; falta aislar la transacción que conserva el bloqueo. La corrección
  anterior `list(self.archive.get_all(...))` en historial de entrada MUC sigue
  instalada; no se reaplicó ni se aumentó el pool.
- **503:** las dos líneas observadas son de WhatsMeow/WhatsApp, no demuestran
  un error de stream XMPP. El upstream contempla auto-reconexión para ese caso:
  [manejo oficial de conexiones](https://github.com/tulir/whatsmeow/blob/main/connectionevents.go).

## Hipótesis pendientes y límites

- No hay una captura del cliente que atribuya cada interrupción humana a un
  evento particular. Los defectos locales están reproducidos; la causalidad de
  cada incidente histórico no está demostrada.
- `_finish_group_join()` retira la unión fallida y no programa otro intento por
  sí solo; el ping periódico sólo inspecciona grupos marcados como unidos.
  Esto puede dejar un grupo fuera tras un fallo transitorio de join, pero no se
  halló evidencia productiva que justifique reintentar errores indiscriminadamente.
  Slixmpp distingue rechazo de presencia y timeout:
  [contrato MUC](https://slixmpp.readthedocs.io/en/latest/api/plugins/xep_0045.html).
- `XmppService.connect()` puede iniciar otro hilo sin esperar el cierre del
  anterior; es una ruta potencial de solapamiento al cambiar conexión, no una
  causa demostrada del fallo después de varias horas. No se añadió un manager
  ni se reescribió su ciclo de vida como corrección preventiva.

## Validación

Se usó el entorno Conda `XMPP`, Slixmpp instalado 1.16.0. Resultados en
`.test-results/`, ignorados por Git; no se copiaron conversaciones ni secretos.

| Comprobación | Resultado |
|---|---|
| Baseline `--suite all --area connection --area bridge` | 259 métodos / 117 subcasos, aprobados antes de editar |
| `--suite all --match '*test_whatsapp_linking*'` final | 34 métodos / 13 subcasos, aprobados; nueve métodos nuevos, mismo catálogo |
| Ruff completo y `git diff --check` | Aprobados |
| `--suite all` monolítico | Cierre `0xC0000005`, sin JSON; no aprobado |
| Áreas automation, connection, history, media, messaging, storage, testing, en procesos separados | 102, 102, 25, 179, 247, 64 y 1 métodos, respectivamente, aprobados |
| Área bridge, ejecución final amplia | 166 métodos; un fallo en `VoiceMediaIsolationTests.test_logout_and_device_change_revoke_worker_without_cross_thread_session_access`, subcaso `device_change` |
| Diagnóstico aislado de ese caso de voz | 1 método / 2 subcasos, aprobados; no anula el fallo amplio ni demuestra ausencia de carrera |
| Área platform combinada | Mismo cierre nativo sin JSON |
| Platform por módulos aislados | 178 métodos en procesos aprobados; `test_ui_event_pump` registra sus 11 métodos sin assertions fallidas, pero el proceso termina con código −1: no aprobado |

El inventario es de 1.075 métodos. Las selecciones por áreas/módulos cubren el
inventario, pero **no equivalen a una suite completa aprobada** por los dos
problemas descritos. El cierre combinado de wx y el cierre del módulo del pump
ya están documentados en `VINCULACION_WHATSAPP.md`; no se atribuye automáticamente
esta ejecución a la misma causa sin un diagnóstico nativo adicional.
La prueba de voz envía un frame antes de revocar/cambiar dispositivo; el orden
entre hilos puede explicar la variación. No se relajó ni eliminó su assertion.

## Archivos y siguiente verificación

- `cliente_xmpp/ui/main_window.py`: presentación transitoria y generaciones.
- `cliente_xmpp/xmpp/events.py`: generación opcional en el evento existente.
- `cliente_xmpp/xmpp/client.py`: emisión del valor y errores de discovery.
- `cliente_xmpp/xmpp/linking.py`: continuidad y aislamiento del sondeo.
- `tests/test_whatsapp_linking.py`: regresiones enfocadas, fixtures ficticios.
- Este informe: evidencia y límites de entrega.

Pendiente validación humana tras relanzar CAN desde el código: mantener un chat
abierto durante recuperación, comprobar foco/NVDA, entrada y salida individuales
y grupales, y confirmar que sólo un logout real exige vinculación. No se hizo
esa prueba ni se envió nada por cuenta del usuario. `sent` no prueba entrega.
Cambiar watchdog o reparar/desplegar el binario nativo requiere una operación
posterior aprobada, con pruebas aisladas y conservación de sesiones/datos.
