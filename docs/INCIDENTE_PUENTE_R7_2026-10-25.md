# R7: corrección del archivo grupal y supervisión funcional

La reproducción aislada confirma que R6 todavía bloqueaba el bucle de eventos en
`Participant._send → MessageArchive.add → commit`. La corrección amplía la ruta
asíncrona existente, conserva la persistencia antes del envío y añade supervisión
funcional. La pérdida simultánea de SSH/NVDA Remote sigue siendo una investigación
separada: no se ha reproducido ni demostrado su causa.

## Cambios

| Área | Corrección y límite |
|---|---|
| Participantes | La API síncrona conserva su retorno de stanza; una copia se archiva mediante `add_async`, con sesión SQL propia en el worker, antes de distribuirse por XMPP. |
| Orden | Mensajes, recibos y ecos usan el mismo bloqueo por sesión/sala. El eco cede al bucle para no adelantar tareas de participantes ya encoladas. No se reenvían resultados inciertos. |
| Callbacks nativos | `delivery_scope` espera sólo los archivos generados por ese callback. No hay una barrera global entre cuentas o salas. Excepciones y cancelación no se convierten en éxito silencioso. |
| Lectores MAM | `get_all` transforma a objetos de historial independientes y cierra la sesión antes del primer `yield`. Se conservan argumentos, filtros, orden y límites existentes. |
| Salud | Socket Unix privado, atendido por el bucle principal, con ping XMPP de ida/vuelta al servidor y detección de fallo local de archivo. No depende de una cuenta WhatsApp particular. |
| Recuperación | Docker exige tres fallos consecutivos; el watchdog conserva cooldown de 15 minutos, limita a dos intentos por hora y registra evidencia acotada antes del reinicio. |

No se cambiaron WAL, timeouts SQLite, dependencias, biblioteca nativa Go,
credenciales, cuentas, sesiones, permisos de datos ni bases. No se aumentaron
recursos ni se modificaron SSH, Nginx, NVDA Remote, Mailcow, UFW o Fail2ban.

## Reproducción y pruebas

- La imagen base exacta es R6, ImageID
  `sha256:a772d3842cadcc1f72a85379b391e80190f5ddf6aebb44fae8e00d11babd51a8`.
- El mismo smoke reproduce el fallo en R6 antes de aplicar el parche y pasa
  después, sin red ni datos productivos y ejecutándose como `slidge`.
- Casos de contención transitoria y persistente, rollback/conexiones cerradas,
  ausencia de envío tras fallo de commit, snapshot frente a mutación del caller,
  mensajes/recibos/historial/eco, deduplicación del archivo y lector suspendido.
- El alcance por callback tiene pruebas de aislamiento, propagación de errores
  y espera de limpieza durante cancelación.
- Salud positiva, componente desconectado, timeout XMPP y fallo SQL local.
- Seis smokes del build y once smokes adicionales de medios, stickers,
  información de mensajes, lectura, números y llamadas. No acreditan llamadas,
  mensajes o transcripciones reales por WhatsApp.
- El watchdog pasa siete escenarios con Docker/logger ficticios; no se detuvo
  ni se colgó deliberadamente producción para probar su recuperación.

La suite completa y la identidad finalmente desplegada se registran en la
sección de entrega cuando termina el corte. Los diagnósticos locales van en
`.test-results/stability-r7/`, ignorado por Git.

Resultado final previo al corte: **1 140 métodos / 661 subcasos**, sin fallos,
errores ni omisiones; Ruff y `git diff --check` aprobados. Una ejecución anterior
tuvo seis fallos intermitentes de foco de modales; el módulo aislado y las
repeticiones completas posteriores pasaron sin modificar UI o programación.

Una fixture Docker adicional bloqueó su propio bucle 40 segundos, pasó de
`healthy` a `unhealthy` y volvió a `healthy`, con cero reinicios. Reproduce
sondas abandonadas sin que sus sockets cerrados generen excepciones fatales.

## Corte protegido y reversión

El procedimiento realiza backups SQLite mediante la API online y comprueba
`quick_check`; guarda Compose, watchdog e inspección en un directorio de root
con modo 700. Cambia únicamente la imagen del servicio `slidge-whatsapp`, con
`--no-deps`, y verifica que opciones, variables, montajes y contenedores ajenos
se conserven. Una aceptación requiere respuestas funcionales consecutivas y
salud Docker positiva; cualquier fallo ejecuta la reversión del puente.

La reversión restaura imagen/configuración, **no una base antigua**. No borra
mensajes, sesiones, respaldos ni artefactos ajenos.

El primer corte se revirtió automáticamente porque la comparación trataba el
orden de `Env`/`Binds` como cambio funcional. Se comprobó que sólo cambió el
orden y se normalizó la comparación. Un segundo intento se detuvo antes de
mutar producción por un error readonly del backup; después se verificaron
`quick_check=ok`, modo `delete`, UID 10000 y ausencia de journal remanente. No
se alteraron permisos para ocultar ese resultado ni se restauraron datos viejos.

Otro candidato se revirtió al detectar un reinicio durante startup. En staging
se reprodujo que responder a una sonda vencida podía lanzar `BrokenPipe` o
`ConnectionReset` al manejador fatal del gateway. La imagen final absorbe esos
errores de desconexión. No se conservó la traza exacta del candidato retirado:
la reproducción demuestra el defecto, no permite atribuir retrospectivamente
cada excepción de aquel arranque.

## Observación de estabilidad

- Captura acotada de 26 horas en el servidor: identidad/salud del contenedor,
  errores agregados, presión CPU/memoria/I/O, conntrack, contadores TCP e interfaz
  y llegada de SYN a SSH/XMPP/HTTPS/NVDA Remote. Archivos rotados y privados.
- Sondas externas desde Windows: banner SSH, HTTPS verificado, XMPP
  STARTTLS/certificado/mecanismos SASL y TCP de NVDA Remote. No autentican NVDA,
  no controlan escritorios y no envían mensajes.
- No se conservan cuerpos de conversaciones, audio, credenciales ni paquetes
  completos. La captura SYN retiene contadores, no direcciones.
- Las dos vistas permiten correlacionar fallo externo con recepción local y
  presión del host; no equivalen a dos redes externas independientes.
- No considerar diez minutos saludables como resolución de una recurrencia
  aproximada de doce horas. Informar duración real, huecos y límites.

## Entrega

**Corte aceptado el 25 de octubre a las 12:58:51 de Ciudad de México**
(`18:58:51Z`), tras respuestas funcionales consecutivas y salud Docker positiva.

- Imagen local: `cliente-xmpp-bridge:archive-delivery-r7-staging-20261025`.
- ImageID: `sha256:d50d94cd842e49f4facb1bc15058f5cbb3e99049da983d5713508c3a9522b922`.
- Contenedor: `63a1cafdab01df090a67c9e7fceb68a10311793916297046c98a174f7b229150`.
- Respaldo y reversión: `/opt/xmpp/backup-r7-20261025T185845Z/rollback.sh`.
- Evidencia privada: `/opt/xmpp/archive-delivery-r7-20261025/deployment.json`.
- Los otros 26 contenedores conservaron identidad; el watchdog quedó activo.
- En la aceptación: cero reinicios, errores SQL, fallos de archivo o crashes.

**Observación prolongada en curso:** no se declara aún cerrada la recurrencia
de doce horas ni la causa del fallo conjunto de red. No se ha creado build
Windows, tag Git, release ni publicación GHCR. El usuario autorizó posteriormente
commit y push de las correcciones y su evidencia; esa entrega Git no cierra
los problemas pendientes del cliente ni la investigación de red.

### Recaída del cliente reportada después del corte

Aproximadamente a las 13:32 de Ciudad de México el usuario volvió a necesitar
cerrar y abrir CAN y reportó que los no leídos se marcaron como leídos al abrir.
**Esto impide declarar resuelto el problema completo.** En esa ventana el
puente conservó identidad y arranque, salud positiva, cero reinicios y cero
errores SQL/de archivo/fatales en 70 muestras, con puertos locales accesibles.

Se investiga la capa cliente y la reconciliación de no leídos por separado.
No se reinició el cliente desde la auditoría ni se restauraron lecturas en
bloque. La causa concreta de esa nueva recaída aún no está identificada.

### Primera caída externa capturada

El 25 de octubre a las **13:40 de Ciudad de México** (`19:40Z`) las cuatro
sondas externas pasaron simultáneamente a timeout: SSH 12400, HTTPS 443,
XMPP 5222 y NVDA Remote TCP 6837. La última muestra anterior, a `19:39:09Z`,
era positiva en los cuatro puertos. Falló también una conexión SSH real.

Esto confirma el síntoma desde esa red externa, pero no identifica su causa:
durante la caída todavía falta recuperar y contrastar la captura del servidor.
No se ordenó un reinicio para ocultar el incidente y no se atribuye a SQLite,
al proveedor, a un bloqueo de origen o al acceso local sin esa correlación.

## Referencias

- [Transacciones SQLite y COMMIT ocupado](https://www.sqlite.org/lang_transaction.html).
- [Semántica de HEALTHCHECK de Docker](https://docs.docker.com/reference/dockerfile/#healthcheck).
