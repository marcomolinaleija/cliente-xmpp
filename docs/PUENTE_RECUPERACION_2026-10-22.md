# Recuperación del puente: imagen corregida y despliegue controlado

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
