# Información de mensajes propios en grupos — v33 publicada

Versión estable publicada el 12 de octubre de 2026 en GHCR. La prueba privada
de recuperación descrita abajo usa otra imagen candidata:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v33@sha256:3f588f68247239a08c991c08eeb8195ff361cd887053a9c046fb60946ae555fa
```

El manifiesto estable `tools/wsl-appliance/bridge-update-manifest.json` fija esa
referencia. Info fue validada por el usuario; los límites de duración de llamadas
contestadas en móvil se documentan por separado y no se presentan como resueltos.

CAN agrega **Info...** al menú contextual de mensajes propios de grupos. El diálogo
muestra personas, estado y horas confirmadas de entrega/lectura. Conserva selección
y posición al actualizar; Enter abre el detalle de una persona y Escape cierra.
La lista contiene tres encabezados, en orden: **Leído**, **Entregado** y
**Sin entrega confirmada**. Cada uno muestra su cantidad y las personas debajo,
ordenadas por nombre dentro de esa sección. Los encabezados vacíos permanecen
visibles y Enter sobre ellos no abre un detalle de persona. Cada persona conserva
las columnas de hora de entrega y lectura, sin deducir una hora faltante.

## v34: corrección de lectura grupal (14 de octubre de 2026)

Tras la validación del usuario se publicó la segunda candidata como `v34`, sin
reconstruir la imagen probada. El manifiesto estable del puente fija:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v34@sha256:9efc875341933962a29a5ce1b0e233cac4e59e8c65463fed079c9ab5018c9114
```

CAN 1.4.21 incluye la clasificación de avisos de lectura descrita aquí. El detalle
de candidatos siguiente conserva el proceso de construcción y sus límites.

La lectura automática se conserva. El candidato privado construido con
`tools/Dockerfile.bridge-read-recovery-candidate` parte de la v33 publicada y
intenta recuperar únicamente la colección de estado afectada cuando Whatsmeow devuelve
`ErrMismatchingLTHash`: descarga un snapshot con verificación de integridad y
reintenta una vez el mismo horizonte de lectura. El flujo nativo tiene un plazo
de 45 segundos. No cambia dependencias, claves, vinculación ni otras colecciones;
no desactiva validaciones MAC/hash ni envía notificaciones de reset de dispositivos.

Si falla, la lectura remota sigue pendiente y se aplica una pausa de 60 segundos
por cuenta, sin bloquear otras cuentas. El siguiente marcador después de esa
pausa vuelve a intentarlo; no existe un reintento automático por temporizador.
Los privados no ejecutan esta operación de estado grupal.

El cliente desde el código distingue errores de marcadores de lectura de los
errores de envío real: muestra el detalle en la barra de estado y anuncia la
lectura pendiente como máximo una vez por minuto, sin diálogo modal ni retirar
mensajes optimistas. Esta corrección del cliente no modifica un EXE ya instalado.

La validación aislada usa `tools/bridge_read_state_recovery_test.go` (Go/race),
`tools/smoke_bridge_read_state_runtime.py` (ORM y binding instalados) y las pruebas
Python registradas en `tests/suites.json`. No acredita lectura real en WhatsApp:
la prueba humana debe abrir grupos y verificar el estado en el teléfono, junto
con recepción nueva. Construir o probar una candidata no equivale por sí solo
a una publicación estable; v34 se promovió después de la autorización explícita.

La segunda candidata usa `tools/Dockerfile.bridge-read-horizon-candidate` y
conserva el código nativo de la primera. Corrige dos casos detectados en la prueba
real: Slixmpp elimina por defecto el marcador de las respuestas de error, y Slidge
puede guardar la identidad nativa en `group_msg` mientras MAM conserva sólo el ID
XMPP y la fecha. Las respuestas de lectura conservan ahora el marcador; además,
el cliente correlaciona ID del sobre y destinatario durante 120 segundos, con un
máximo de 256 entradas y limpieza al desconectar. Un cuerpo de mensaje real o
un destinatario distinto nunca se clasifica como lectura por esa correlación.

El horizonte se resuelve por las identidades y mapas de la misma sala; IDs
desconocidos o contradictorios siguen pendientes, sin convertirlos en «leer todo».
La recuperación de snapshot falló en la prueba humana de la primera candidata:
no se presenta como resuelta. `CAN_READ_STATE ... error_kind=...` informa sólo una
categoría acotada de fallo, sin mensajes, IDs, claves ni errores completos.

## Ajustes locales del cliente (sin cambiar el puente)

El código del cliente también ofrece **Info...** en mensajes propios de chats
individuales. Esta vista muestra destinatario, fecha del mensaje y estado local
(enviando, no enviado, enviado, entregado o leído); se actualiza cada dos segundos
desde la memoria de CAN, sin consultas nuevas al puente. Las horas de entrega y
lectura figuran como **No registrada en CAN**: el estado no proporciona esos
horarios. Escape cierra el diálogo. Un cambio de cuenta detiene la actualización
y retira la información anterior. La ausencia de un acuse no demuestra un fallo.

Para mensajes de grupos enviados desde el teléfono, CAN conserva por sala la
identidad propia anunciada por la presencia MUC (estado 110 y occupant-id), aunque
el apodo asignado sea diferente del usuario XMPP. Estas presencias se reciben por
el evento nativo `groupchat_presence`: Slixmpp suprime los eventos genéricos de
presencia para los ocupantes MUC. No es necesario igualar el nombre ni las tildes
con el perfil de WhatsApp. Los mensajes propios no deben
incrementar no leídos y habilitan las acciones propias, incluida Info. Una copia
de historial con autoría confirmada puede corregir la clasificación previa del
mismo ID; no se reclasifica por coincidencia de texto ni se borran mensajes o
contadores antiguos de forma masiva. No hay migración sobre conversaciones reales.

Estos ajustes son cambios de fuente del cliente, no una publicación de binarios
ni una actualización del puente.

## Prueba manual

1. Ejecutar CAN desde el código actualizado con el entorno `XMPP`.
2. Enviar un mensaje nuevo a un grupo de prueba y abrir **Info...** en ese mensaje.
3. Comprobar con otro participante los cambios de entrega y lectura; la consulta
   se actualiza cada dos segundos mientras el diálogo esté abierto, sin consultas superpuestas.
4. Cerrar y abrir el diálogo; desconectar CAN y comprobar que conserva la copia
   local. Confirmar teclado, foco y lectura con NVDA.
5. Confirmar que mensajes entrantes no ofrecen Info. En un mensaje propio de chat
   individual, comprobar el estado local y el aviso de horas no registradas.
6. Enviar desde el teléfono texto y audio a un grupo de prueba: deben aparecer
   como propios sin sumar no leídos. Comprobar Info y Eliminar sin efectuar una
   eliminación real salvo autorización. Revisar también texto, archivos,
   respuestas y stickers, con el foco conservado.

No hay mensajes automáticos de prueba enviados a grupos reales. La validación
humana de acuses nuevos y NVDA es independiente de las pruebas automatizadas.

## Respuesta y lectura con el chat abierto

La consulta de Info empieza sin esperar la cola de almacenamiento de historial.
La respuesta se muestra antes de guardarla; una copia local tardía no sustituye
la respuesta actual ni hace retroceder los acuses. El refresco de dos segundos
es el intervalo de consulta, no una garantía del tiempo de llegada de un acuse.

Al abrir un chat, CAN limpia sus no leídos y envía el marcador hasta el último
mensaje entrante conocido. Cada mensaje entrante en vivo recibido mientras esa
conversación está visible avanza el marcador. No se necesita escribir ni salir.
Otros chats, conversaciones ocultas y eventos históricos `notify=False` no
activan esta nueva regla; los marcadores repetidos siguen siendo idempotentes.

Prueba adicional: abrir un chat con tres no leídos, recibir un cuarto dentro,
comprobar cero no leídos en CAN, salir y recibir otro: éste debe quedar no leído.
Repetir en un grupo y un chat individual; comparar aparte con WhatsApp oficial.

La auditoría del 12 de octubre de 2026 encontró que `SendReceipt` sólo llamaba a
`whatsmeow.MarkRead` (acuse por mensaje), sin actualizar explícitamente el estado
leído del chat entre dispositivos. La corrección grupal agrega un método nativo
separado, `MarkChatRead`, que envía `BuildMarkChatAsRead`/`SendAppState` con el ID,
remitente LID y hora original del último mensaje entrante. No usa la hora actual
como horizonte ni interpreta un ID desconocido como «leer todo».

La cola de acuses grupales se consulta sin consumirla; cada ID se confirma sólo
tras un envío exitoso. Los envíos nativos se ejecutan fuera del bucle de red,
con hasta tres intentos. La actualización del estado del chat ocurre una vez por
horizonte, después de los acuses; las publicaciones propias reflejadas no generan
un bucle de sincronización. Otros adaptadores y la ruta individual se conservan.

Estas protecciones no sustituyen la prueba humana del contador oficial. Confirmar
en WhatsApp oficial que abrir el grupo desde CAN quita los pendientes, que otro
mensaje dentro vuelve a quedar leído y que uno posterior a salir queda no leído.

## Qué significan los estados

| Estado | Evidencia |
|---|---|
| Leído | Acuse de lectura de esa persona. |
| Entregado, sin lectura confirmada | Acuse de entrega, sin acuse de lectura. |
| Sin entrega confirmada | Destinatario conocido, todavía sin acuse disponible. |

**La falta de un acuse no demuestra que el mensaje no haya llegado.** Una fecha
desconocida no se deduce de la hora de lectura ni de la hora actual.

El puente captura la lista de destinatarios **antes del envío desde CAN**, usando
la membresía conocida por el puente. Si esa lista no está completa, lo informa.
Nunca utiliza los miembros actuales como destinatarios de un mensaje antiguo.
Los mensajes de otros dispositivos o anteriores al candidato pueden tener sólo
acuses parciales; se muestran como lista incompleta, no como negativos inventados.

## Contrato y persistencia

- Consulta IQ `get` al JID del grupo, namespace
  `urn:can:whatsapp:message-info:0`, elemento `info` con `id` y referencia opcional.
- El puente limita cada petición a la sesión registrada del solicitante, una sala
  existente de esa sesión y un mensaje de autoría propia comprobada. Resuelve IDs
  de origen, eco y archivo por sala, no por texto o nickname.
- La respuesta conserva el ID solicitado, el ID nativo y
  `recipients-complete`. Cada `participant` contiene JID/LID, nombre y timestamps
  Unix confirmados `delivered-at` / `read-at`.
- El puente guarda sólo identidades y acuses en
  `$HOME_DIR/message-info.sqlite3`, privado, WAL y con un ejecutor dedicado serial.
  No guarda cuerpos ni credenciales; no modifica tablas de cuentas de Slidge.
- CAN guarda una caché por cuenta/chat/mensaje en `group_message_info`, versión
  de esquema 21. Lecturas y escrituras usan su ejecutor de almacenamiento; sólo
  el hilo wx actualiza controles. Borrar mensaje/chat borra su caché asociada.
- Las fusiones conservan lectura/entrega y unifican alias PN/LID. Un timeout,
  cambio de cuenta o respuesta tardía no convierte datos desconocidos en estados
  negativos ni actualiza un diálogo cerrado.

También se corrigen dos errores del adaptador: tipos de acuse desconocidos ya no
se interpretan como entrega, y el historial usa timestamps individuales en lugar
del estado global del mensaje. Los acuses de historial repetido enriquecen Info
antes de la salida temprana de deduplicación, sin insertar mensajes nuevos.

## Construcción y entrega controlada

`tools/Dockerfile.bridge-message-info-v33` parte del digest verificado de `v32`.
Ejecuta pruebas Go, race, compilación nativa y smokes Python del candidato y de
avatar, audio, paquetes, stickers y grupos. El paralelismo Go está limitado a dos.

El candidato con Info y sincronización grupal se llama
`cliente-xmpp-bridge:read-state-v33-candidate`.
La iteración que añade la corrección de llamadas entrantes se llama
`cliente-xmpp-bridge:call-duration-v33-candidate`; conserva todas estas funciones.
Su prueba y límites están en [llamadas entrantes v33](PUENTE_WHATSAPP_LLAMADAS_ENTRANTES_V33.md).
Su override de Compose cambia únicamente la imagen de `slidge-whatsapp`, sin
cambiar cuentas, credenciales, volúmenes, Prosody ni el servicio de adjuntos.
Durante esas pruebas, `compose.yml`, el manifiesto estable y la referencia `v32`
permanecieron intactos. Tras la autorización de cierre se publicó la imagen exacta
del último candidato (`call-recovery-v33-candidate`), se verificó su descarga
anónima y se fijaron el Compose base y el manifiesto estable a `v33` por digest.

El candidato anterior con Info se conserva como rollback inmediato, mediante
`/opt/xmpp/message-info-candidate-20261011/candidate.override.yml`:

```sh
cd /opt/xmpp
docker compose -f compose.yml \
  -f message-info-candidate-20261011/candidate.override.yml \
  up -d --no-deps slidge-whatsapp
```

El Compose base ahora usa v33: ejecutarlo solo **ya no revierte a v32**. Para volver
a v32 sin Info, usar el override de rollback conservado:

```sh
cd /opt/xmpp
docker compose -f compose.yml \
  -f release-v33-20261012/backup/rollback-v32.override.yml \
  up -d --no-deps slidge-whatsapp
```

No restaurar bases antiguas como parte de un rollback de imagen: se perderían
mensajes/sesiones posteriores al respaldo. La base nueva queda preservada y `v32`
la ignora. Toda restauración de datos requiere una decisión específica.

## Validación automatizada

```powershell
conda run -n XMPP python tools/run_tests.py --suite all --match '*message_info*'
conda run -n XMPP python tools/run_tests.py --suite all
conda run -n XMPP python -m ruff check .
git diff --check
```

Estas comprobaciones no acreditan por sí solas entrega/lectura real, NVDA ni una
publicación en GHCR. La imagen candidata y la publicación estable son pasos
separados; este documento no autoriza ejecutarlos por sí mismo.
