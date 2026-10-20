# Experimento de voz sobre el puente v35

Rama: `voice-calls`.
Base de código: `f92cee0` (publicación del puente v35).

## Estado actual y colaboración

El experimento ya incluye integración con CAN en Windows, controles de llamada,
audio dúplex y autorización/transporte en el puente Linux. Se han reportado llamadas
reales con WhatsApp oficial; persisten microcortes cuya causa no está cerrada.
Las secciones siguientes conservan el alcance y la receta del primer ensayo offline.

La [guía de voz](../../docs/PUENTE_WHATSAPP_VOZ_EXPERIMENTAL_V35.md) detalla los
componentes, permisos del micrófono, pruebas Python/Go y cambios posteriores del
transporte. La [investigación de calidad](../../docs/INVESTIGACION_CALIDAD_LLAMADAS_2026-10-18.md)
recoge las divergencias MLow y las mediciones pendientes. La validación manual de
teclado/NVDA y de interoperabilidad en distintas redes sigue pendiente.

Para contribuir, crea tu propia rama desde `voice-calls`. Usa fixtures sintéticos
y, para llamadas reales, una cuenta y un puente de prueba propios. No incluyas
credenciales, conversaciones, logs privados ni grabaciones en Git. La configuración
de voz y las activaciones históricas documentadas no otorgan acceso al VPS existente.

## Alcance de esta primera prueba

Comprobar la compilación del motor de [WaCalls](https://github.com/JotaDev66/WaCalls)
contra **las fuentes vendorizadas de WhatsMeow extraídas de la imagen v35**, y
ejecutar una cadena de medios con audio sintético en ambos sentidos:

```text
PCM sintético -> MLow -> RTP -> SRTP -> verificar/descifrar -> MLow -> PCM
```

El resultado comprueba tamaño de los frames, muestras finitas, salida distinta
de silencio y rechazo de un paquete alterado. También construye el adaptador
para un cliente WhatsMeow existente sin conectar ni crear otra vinculación.
Las pruebas normales y con detector de carreras de Go se ejecutan aparte.

Esto **no acredita una llamada real**, recepción en un teléfono, calidad de voz,
latencia, conectividad entre redes, ni integración con la UI Windows. El binario
no acepta cuentas ni destinos y no expone puertos. No es una imagen de Slidge
para sustituir el servicio productivo.

## Versiones e historial

`upstream.json` fija los dos digests de imágenes, el commit y checksum del código
WaCalls, y la revisión esperada de WhatsMeow. La construcción comprueba el SHA-256
del archivo antes de extraerlo y exige que la dependencia de WaCalls coincida con
la declarada en el `go.mod` de v35. Después aplica un `replace` al código real de
WhatsMeow de esa imagen: no basta con compilar contra una descarga sin sus parches.

El puente existente tiene historial en este mismo repositorio: Dockerfiles,
parches Python/Go, pruebas y documentos operativos. Los commits `ac74f06`, `592db58`
y `f92cee0` recogen, respectivamente, registros de llamadas v27, cambios de v33 y
la corrección/publicación de v35. Las imágenes tienen tags y digests; su existencia
no sustituye conservar las recetas y los parches correspondientes en Git.

WaCalls se utiliza con su licencia MIT; su archivo `LICENSE` y el del codec MLow
se conservan en el código descargado dentro de la imagen experimental. No se
incorpora el repositorio externo completo a Git.

## Ejecutar

Se requiere un Docker Linux ya disponible. En Windows puede usarse una distribución
WSL existente con Docker. El entorno Conda `XMPP` debe existir; este runner no lo
crea ni instala paquetes Python. Go y sus dependencias viven en la imagen aislada.

Desde la raíz de esta rama:

```powershell
conda run -n XMPP python tools/run_voice_call_probe.py --build --wsl Ubuntu
```

Con Docker directamente disponible:

```text
python tools/run_voice_call_probe.py --build
```

`--build` construye únicamente `cliente-xmpp-voice-probe:v35-offline`, descargando
las imágenes fijadas, el archivo de fuente verificado y las dependencias Go.
Sin `--build`, se verifica y usa la imagen experimental ya construida. El contexto
contiene sólo el archivo fuente público, su checksum, el Dockerfile y los archivos
Go de `canprobe`: no se envía el árbol completo del cliente al daemon Docker.

La ejecución de pruebas y del binario utiliza `--network none`, sin montajes,
puertos publicados ni archivos de credenciales. No hay acceso a volúmenes de
producción. Los logs completos y el resumen JSON se guardan en
`.test-results/voice-call-probe/`, ignorado por Git. Una selección vacía, salida
incompleta, un fallo Go o un resultado de medios inválido devuelve error.
La imagen conserva la preparación de las variantes normal y race para no repetir
la compilación de dependencias en cada ejecución del experimento.

Las pruebas Python del runner están en el catálogo, área `bridge`:

```text
conda run -n XMPP python tools/run_tests.py --suite all --area bridge --match '*VoiceCallProbeTests*'
```

## Integración experimental añadida

El actor `voicecore`, el proxy autenticado WSS y el transporte asyncio del cliente
se describen en [el contrato de voz v35](../../docs/PUENTE_WHATSAPP_VOZ_EXPERIMENTAL_V35.md).
La reconstrucción aislada del binding se solicita con `--build --integration`.
El experimento de códec anterior sigue siendo una comprobación separada.
El contexto incluye regresiones de retirada del motor, configuración/limpieza
de relays y propagación de fallos de rechazo, fin y aceptación. El parche de
ciclo de vida se genera desde el archivo verificado; no se aplica a producción.

## Comprobaciones pendientes con cuenta de prueba

La captura/reproducción nativa, controles accesibles, silencio y selección de
dispositivos ya están integrados y probados sin cuenta; la evidencia actual está en
[el contrato de voz v35](../../docs/PUENTE_WHATSAPP_VOZ_EXPERIMENTAL_V35.md).

La interoperabilidad necesita una prueba separada con cuenta de WhatsApp de
prueba y destinos expresamente autorizados. Mientras no exista esa autorización,
se continúa únicamente con fixtures, simulación y transporte local:

1. Verificar negociación y ciclo de llamada del motor integrado con WhatsApp real.
2. Acreditar audio audible bidireccional entre Windows y un teléfono de prueba.
3. Probar llamadas entrantes/salientes entre redes distintas, rechazo y fin desde ambos extremos.
4. Comprobar que los mensajes y el teléfono oficial siguen funcionando durante y después.
5. Completar navegación manual con teclado/NVDA y cambios reales de dispositivos
   durante una llamada, además de las regresiones automatizadas ya presentes.

Los estados y metadatos de llamada existentes seguirán separados de la evidencia
de audio: un `accepted`, una fila de historial o un teléfono que suena no prueban
audio bidireccional.

## Evidencia de ejecución

La primera ejecución detectó que `SrtpContext.Unprotect` en el commit fijado de
WaCalls descifraba sin comprobar el HMAC. El caso negativo de la prueba falló:
aceptaba un paquete con el tag de autenticación alterado.

`wacalls-srtp-auth.patch` incorpora sólo al experimento una comprobación HMAC
en tiempo constante antes de descifrar o actualizar el estado de rollover. La
construcción exige que el parche aplique limpiamente sobre la fuente fijada.
Esto no es una auditoría completa de SRTP: quedan por validar interoperabilidad,
protección contra replay y recepción reordenada antes de una llamada real.

Pruebas realizadas el 17 de octubre de 2026, sin cuenta real:

- Python, toda el área `bridge`: 123 métodos y 48 subcasos aprobados.
- Ruff del repositorio: aprobado.
- Go: 62 pruebas aprobadas; con `-race`: 62 aprobadas.
- En cada modo Go hay dos omisiones declaradas por el propio upstream:
  `TestSmplTablesCDF` y `TestSynth`. Sus fuentes las derivan a otras comprobaciones;
  este experimento no elimina, desactiva ni cambia esas pruebas.
- Cadena sintética: 40 frames y 38 400 muestras decodificadas en dos direcciones,
  con salida finita distinta de silencio. Rechazo de modificaciones en cabecera,
  contenido y tag; aceptación del siguiente paquete válido y del rollover.

El resumen completo está en `.test-results/voice-call-probe/summary.json` y conserva
`live_call_verified: false`. Ninguna de estas pruebas acredita interoperabilidad
con WhatsApp ni calidad de conversación con personas.

## Instrumentación y corpus de referencia (18 de octubre)

El ensayo de diagnóstico usa etiquetas locales separadas:
`cliente-xmpp-voice-probe:v35-metrics-offline` y
`cliente-xmpp-bridge:v35-metrics-local`. Conserva las imágenes anteriores.
Para construir y validar esta variante sin cuenta ni despliegue:

```powershell
conda run -n XMPP python tools/run_voice_call_probe.py --build --codec-reference --integration --wsl Ubuntu
```

La opción `--codec-reference` descarga exclusivamente tres fixtures públicos de
audio sintético, fijados por commit, longitud y SHA-256 en
`codec-reference.json`. Se guardan bajo `.test-results/`; no se añaden grabaciones
ni binarios externos al repositorio. El corpus upstream declara expresamente
que no contiene conversaciones grabadas ni datos personales; su procedencia
está enlazada en ese manifiesto. La ejecución del ensayo usa `--network none`,
sin volúmenes de usuario, dispositivos de sonido o argumentos de cuenta.

La comparación distingue dos direcciones de evidencia:

- Paquetes del códec de referencia → nuestro decodificador → comparación con
  PCM del decodificador de referencia: error RMS, correlación y coincidencias
  PCM16, incluyendo el primer bloque después de actividad inactiva.
- PCM sintético compartido → nuestro codificador: distribución de TOC y
  diferencias con las marcas de actividad de referencia. No exige igualdad
  completa de paquetes entre codificadores con análisis/ajustes diferentes.

`outgoing_independent_decode_verified: false` significa que el ensayo todavía
no alimenta nuestros paquetes salientes al decodificador original de WhatsApp.
Las diferencias obtenidas son un diagnóstico, no una aprobación de calidad.
El códec y el tamaño/política de las colas no se modifican en esta fase.

Con `CAN_VOICE_METRICS=1`, el motor Go emite `CAN_VOICE_NATIVE` cada diez segundos
mientras existe una llamada y al terminar. Sólo contiene una etiqueta aleatoria
por llamada, contadores y slots numéricos de relay; no contiene call-id, JID,
direcciones, errores libres, claves ni muestras. La escritura ocurre en otro
goroutine mediante una cola acotada; `reports_dropped` revela saturación de esa
cola. Los contadores de audio no dependen de que el log consiga escribirse.

- `socket_capture_packets`, `capture_queue_dropped_packets`,
  `capture_inactive_packets`: entrada al socket, descarte de cola y retirada
  antes de consumir por no estar activa la llamada.
- `pcm_consumed_blocks/samples`, `pcm_not_ready_blocks/samples`: audio entregado
  al acumulador del códec o rechazado por falta de códec/relay/criptografía.
- `pcm_gap_max_us`, `rtp_gap_max_us`: pausas reales entre consumos/paquetes
  creados. Se distinguen de la progresión del reloj RTP.
- `encode_frames/errors/total_us/max_us/over_60ms`: intentos, fallos y coste del
  codificador de captura. `keepalive_encode_*` identifica por separado el audio
  de mantenimiento; no se suma a captura como si fuera micrófono recibido.
- `rtp_packets`, `rtp_timestamp_samples`, `rtp_sequence_discontinuities`,
  `rtp_clock_discontinuities`, `protect_error`: creación y protección local.
  Sus discontinuidades no son mediciones de pérdida de red.
- `relay_broadcast_packets`, `relay_no_targets_packets`, `relay_unmeasured` y
  resultados por slot: `accepted`, `failed`, `not_open`, `send_max_us`.
  `accepted` significa que `DataChannel.Send` aceptó los bytes, no que el
  teléfono los recibió. El máximo de slots por informe es 32.

Los contadores son acumulados por llamada; un snapshot entre eventos puede
mostrar transitoriamente etapas distintas. Al finalizar puede quedar audio
parcial en el acumulador o en cola. La cadena actual no expone resúmenes RTCP
autenticados: no se inventan pérdidas/RTT remotos a partir de estos contadores.
`elapsed_ms` puede incluir el timbrado; no equivale a duración de captura.
Las etiquetas Go y del proxy son independientes. Antes de una medición real
con llamadas simultáneas falta integrar su correlación anónima y el lector
acotado del nuevo esquema; no se atribuyen registros sólo por duración parecida.
