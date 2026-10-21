# Voz experimental en v35

Para actualizar desde una versión anterior, empieza por la
[guía de compatibilidad y requisitos de llamadas](ACTUALIZACION_LLAMADAS.md).
Las activaciones y ejemplos siguientes son evidencia histórica; el entrypoint
actual prepara el socket privado automáticamente y no requiere copiar una ruta
de socket de esas activaciones.

Desarrollo aislado en `voice-calls`. El 18 de octubre el usuario
autorizó activar una candidata local en `marco-vps` para probar, sin publicar en GHCR.
Las pruebas usan identidades ficticias, audio generado y servidores locales. No se
vincula una cuenta ni llama a personas. La comprobación nativa opcional de Windows
abre el dispositivo local con captura silenciada y salida cero; no guarda ni envía audio.

## Componentes

- `experiments/voice_calls/voicecore`: actor Go por sesión, adaptador WaCalls para
  el cliente WhatsMeow existente, control de llamadas y colas PCM acotadas.
- `tools/patch_slidge_voice.py`: añade hooks a `Session.setClient`, `clearClient`
  y `handleEvent` de las fuentes reales v35. Valida todos los anchors antes de
  escribir; una base distinta falla. No modifica la interfaz pública de gopy.
- `tools/bridge_voice.py`: autorización XMPP y proxy WSS al socket Unix nativo.
- `cliente_xmpp/xmpp/voice.py`: autorización, verificación TLS, control y audio en
  el bucle asyncio del servicio. Los paquetes PCM no pasan por wx ni SQLite.
- `XmppService.start_voice`, `voice_command` y `stop_voice`: entrada explícita
  desde el hilo del cliente. No se activa automáticamente al conectar XMPP.
- `cliente_xmpp/audio/live_call.py`: dispositivo PortAudio dúplex en un worker
  serial, PCM mono a 16 kHz, colas acotadas y permisos de captura revocables.
- `cliente_xmpp/xmpp/voice_audio.py`: coordina captura/reproducción con asyncio;
  sólo una acción explícita de llamar o contestar permite abrir el micrófono.
- `cliente_xmpp/ui/voice_controller.py`, `voice_dialog.py` y `voice_integration.py`:
  botón **Llamar** junto al contacto, confirmación y ventana de controles/audio.

El puente sigue siendo Linux. Windows aloja el cliente; esta arquitectura no
requiere un VPS Windows.

```text
Cliente Windows -- IQ sobre XMPP/TLS --> Slidge: autorización de su propia sesión
Cliente Windows -- WSS, token único --> proxy Python -- Unix privado --> Go/WaCalls
                                                                      |
                                                      WhatsMeow existente / SRTP
```

El actor no crea otra vinculación, no llama a `Connect` y no desconecta el cliente
compartido. Slidge conserva su ciclo de vida. La retirada de una sesión cancela
la voz sin esperar desde el handler de mensajes. Al perder el canal multimedia,
el actor termina la llamada y libera la exclusividad tras limpiar sus colas.
Se descartan callbacks tardíos de una instancia anterior del motor.

El motor fijado requiere correcciones locales adicionales al HMAC inicial.
`tools/patch_wacalls_voice_lifecycle.py` genera un parche desde el archivo fuente
verificado, con anchors exactos. La instancia retirada cancela sus tareas de
acuse y no admite nuevas llamadas; la configuración de relays se serializa con
su limpieza para impedir reaperturas posteriores. Los acuses salientes se
correlacionan con el ID de la llamada que los originó. Rechazo y fin limpian
medios antes de esperar señalización bajo el plazo del actor; un error no se
informa como éxito. La aceptación requiere una clave y propaga errores de envío.
Estas correcciones tienen pruebas con relays controlados y WhatsMeow desconectado,
sin cuenta, además del detector de carreras. No sustituyen probar SCTP en redes reales.

## Contrato de control y medios

La autorización es un IQ `get` al JID exacto del componente con
`<open xmlns="urn:can:whatsapp:voice:1"/>`. Se exige una sesión registrada,
conectada y correspondiente al bare JID de origen, y un recurso XMPP completo.
La respuesta incluye URL WSS, token, caducidad de 30 segundos, protocolo
`can-voice-1` y formato PCM16LE mono a 16 kHz. Nunca incluye el identificador
del dispositivo vinculado.

El token se conserva sólo como SHA-256 en memoria, sirve una vez y está asociado
a esa sesión, dispositivo y recurso. Otro bootstrap del mismo recurso invalida
el token pendiente anterior. Cerrar sesión revoca tokens y canales. El token
viaja en `Authorization: Bearer`, nunca en URL, historial o logs de acceso.
No se aceptan Origin, tokens en query, certificados no confiables ni
redirecciones del canal. El cliente utiliza las CA del sistema o la CA configurada
para la conexión. No existe un modo `ssl=False`.

Un solo canal controla cada sesión nativa. Los comandos JSON son `dial`, `answer`,
`reject` y `hangup`, con un `id` de correlación. Las últimas tres acciones exigen
el ID de la llamada actual; `answer` y `reject` exigen una oferta entrante. Para
`dial`, Python resuelve exclusivamente un contacto ya conocido de esa sesión,
con JID del componente y sin recurso. Go rechaza grupos y destinos con identidad
de dispositivo. No se aceptan destinos nativos arbitrarios desde el cliente.

Los eventos `state` llevan revisión monotónica, ID, participante, dirección,
estado y motivo. Los eventos del cliente incluyen cuenta y componente para no
mezclar perfiles. Están separados del historial existente de llamadas v27/v35:
una fila histórica no activa el micrófono ni demuestra una conexión multimedia.
Los eventos de UI incorporan también un ID de sesión; eventos, errores y acciones
de un canal anterior no alteran el nuevo. Desactivar o desconectar invalida una
activación pendiente incluso mientras espera el cierre del canal anterior.

El nombre del participante se resuelve únicamente con el almacén LID/PN del mismo
cliente y contactos ya conocidos de esa sesión. Se conserva el alias de roster,
incluidas conversiones existentes para números mexicanos. No se inventan contactos
ni se consulta la red para mostrar un nombre. Si falta correspondencia, la ventana
muestra un contacto genérico en lugar de asociarlo a otra conversación.

La captura usa frames binarios de 640 bytes: 320 muestras, 20 ms. La recepción
admite hasta 1600 muestras por paquete, con tamaño par. El cliente conserva hasta
seis bloques de captura (120 ms) por cola y los envía espaciados 20 ms, incluso
después de una pausa del servicio. La cola de recepción del transporte conserva
tres paquetes; el reproductor limita su reserva por muestras a 240 ms. Ante exceso
se retira audio antiguo. Las colas nativas del puente siguen limitadas a tres paquetes.
Al salir de
`active` se limpia la cola del cliente. El motor conserva su tamaño de frame
MLow y acumula los bloques de captura por su API existente.

## Uso del cliente y propiedad del micrófono

El canal de control se prepara al confirmar la conexión de WhatsApp, sin abrir
ventana ni micrófono. En la conversación individual, **Llamar** está junto al nombre
del contacto; la misma acción aparece en su menú contextual de la lista. No hay una
entrada de llamadas en **Ver** ni un paso manual de habilitación.

Antes de iniciar aparece «Estás a punto de llamar a X. ¿Quieres continuar?», con
**No** como opción predeterminada. Sólo tras aceptar se abre la ventana, con
secciones de controles y dispositivos. El destino confirmado queda fijado aunque
cambie la selección del chat; cambiar de perfil o cancelar impide un marcado tardío.
Si todavía abre el canal, se espera su estado inicial y se marca una sola vez.
Una oferta entrante que llegue durante esa espera cancela el marcado pendiente.

**Contestar** exige la oferta entrante actual. La captura empieza
cuando ese mismo ID alcanza el estado `active`, tras comprobar el formato nativo.
Una llamada aceptada desde otro dispositivo no autoriza captura en Windows.

**Silenciar micrófono** es una casilla nativa con estado marcado accesible. El silencio
se aplica en la captura y antes de enviar paquetes que estuvieran encolados. Un cambio
de silencio mientras el driver comprueba dispositivos no puede quedar sobrescrito
por una configuración anterior. **Aplicar dispositivos** comprueba entrada/salida
antes de reiniciar audio. Una identidad elegida cuyo índice fue reutilizado se conserva
como no disponible: no se captura silenciosamente otro micrófono.

Colgar, cerrar con Escape, desconectar o cambiar de perfil revoca primero
la captura; el worker cierra después el driver. Los callbacks de una apertura tardía
no pueden capturar ni reproducir. La pérdida del dispositivo intenta terminar la
llamada actual. Grabar una nota de voz y una llamada son acciones excluyentes.
Cerrar una llamada mantiene la escucha de nuevas ofertas. Si se cierra mientras
todavía se comprueba audio para llamar/contestar, se revoca esa intención cerrando
su canal y se abre uno nuevo sin autorización de captura.

La ventana es modeless y desplazable, conserva la selección de texto/dispositivos
ante eventos. Las entrantes muestran **Contestar/Rechazar** sin activar la ventana
ni tomar el foco; un contacto silenciado no abre una ventana automáticamente.
Los avisos existentes del
historial conservan las preferencias de mute, toast y sonido; el control de voz no
añade una segunda notificación. No se escribe PCM en historial, eventos wx ni SQLite.

## Cortes: reproducción sintética y diagnóstico local

El 18 de octubre se reprodujeron dos huecos (40 ms en total) con el cliente
anterior: 100 paquetes de 60 ms, variación de entrega de hasta 40 ms y ninguna
pérdida de paquetes. Es evidencia de una debilidad del reproductor, no una medición
de la llamada del usuario ni prueba de que el VPS o las redes estén libres de fallos.

`audio/voice_playout.py` añade una reserva temporal inicial de 60 ms. Si se agota,
reúne audio antes de reanudar y aumenta esa reserva en pasos de 20 ms, hasta 120 ms.
La prueba repite la entrega irregular y verifica los 300 bloques completos, en orden,
sin huecos intermedios ni descartes. Otros casos cubren tamaños variables de paquete,
agotamiento y crecimiento acotado. Esto añade retardo de reproducción deliberadamente;
no cambia el códec ni recupera paquetes perdidos en el tramo WhatsApp.

La captura conserva una pausa de servicio de 100 ms sin descartar sus cinco bloques.
El envío espaciado evita vaciar esa acumulación en una ráfaga hacia el motor nativo.
Las pruebas comprueban que el silencio y el cierre se vuelven a validar después
de esperar el próximo envío: una captura pendiente no puede salir al revocar audio.

**Copiar diagnóstico de audio**, en la ventana de llamada, solicita una instantánea
local por los eventos tipados y copia sólo contadores al portapapeles. No abre otro
dispositivo ni incluye PCM, cuentas, contactos, nombres de dispositivos o mensajes.
No guarda archivos ni envía estadísticas al puente ni a servicios externos.

Incluye descartes de captura/envío/recepción, agotamientos de reproducción, reserva,
avisos PortAudio de entrada/salida, callbacks omitidos, pausas entre paquetes,
espera del socket y retrasos del servicio. Los contadores del dispositivo empiezan
en su última apertura; los del transporte, al llamar/contestar. Las respuestas
se correlacionan por perfil, sesión e ID de solicitud.

Una pausa entre paquetes no distingue por sí sola red, relay WhatsApp y ejecución
del servidor. Tampoco un agotamiento de PCM demuestra pérdida: el silencio remoto
puede dejar de entregar audio. No hay todavía contadores RTP/SRTP remotos en este
diagnóstico. La corrección es del cliente; no requiere cambiar la candidata r2 del VPS.

El socket nativo reside en un directorio privado dentro del HOME administrado de
Slidge: directorio 0700, socket 0600, ambos del usuario del puente. No escucha TCP.
La ruta `CAN_VOICE_SOCKET` debe heredarse al arrancar el proceso: Go conserva una
copia del entorno al cargar gopy y no ve asignaciones posteriores de Python.
Se usa un tmpfs privado nuevo por arranque para evitar sockets antiguos. Python
valida propietario, permisos, ubicación y ausencia de enlaces/archivos existentes;
no cambia esa variable después de cargar Go. No se registra identidad nativa ni audio.

## Construcción y comprobación sin cuenta

Con Docker en una distribución WSL existente, desde la raíz del worktree:

```powershell
conda run -n XMPP python tools/run_voice_call_probe.py --build --integration --wsl Ubuntu
conda run -n XMPP python tools/run_tests.py --suite all --match '*voice*'
conda run -n XMPP python tools/run_tests.py --suite all
conda run -n XMPP python -m ruff check .
git diff --check
```

La primera construcción verifica las fuentes y compila contra WhatsMeow extraído
del digest v35. La segunda añade los hooks a Slidge y reconstruye su biblioteca
gopy, conserva el mapa de símbolos y ejecuta pruebas normales, race e importación.
El runtime experimental se llama `cliente-xmpp-bridge:v35-voice-local` y queda
desactivado por defecto. El runner guarda IDs de imagen y resultados en
`.test-results/voice-call-probe/summary.json`. Los smokes ejecutables no tienen
red externa, montajes ni puertos publicados. La descarga de dependencias ocurre
en la construcción, antes de esas pruebas.

El runner compara también una huella del código del probe con la imagen: no
acepta validar fuentes nuevas usando por accidente una imagen anterior. La
integración debe contener un paquete raíz con pruebas de `Session` ejecutadas;
las pruebas del motor por sí solas no satisfacen esa comprobación.

Las pruebas TLS Windows generan certificados y claves temporales con OpenSSL del
entorno existente; nunca usan certificados ni credenciales personales. El backend
del proxy se sustituye por un fixture TCP loopback porque asyncio Windows no
ofrece UnixConnector. Go prueba por separado el socket Unix real y los hooks de
Slidge. Esta separación debe constar al interpretar los resultados.

La prueba de extremo a extremo usa los botones wx reales, despacho de eventos del
MainWindow, el hilo asyncio de XmppService, autorización y proxy WSS/TLS reales,
un fixture de señalización y un backend PortAudio simulado. Comprueba PCM en ambos
sentidos, silencio y cierre. La señalización WhatsApp sigue siendo simulada.

La inspección nativa de nombres, roles MSAA y casilla de silencio usa la interfaz
[AccessibleObjectFromWindow del SDK de Windows](https://learn.microsoft.com/en-us/windows/win32/api/oleacc/nf-oleacc-accessibleobjectfromwindow).
No añade una dependencia COM. El sondeo puede ejecutarse sin red ni cuenta:

```powershell
conda run -n XMPP python tools/probe_voice_windows.py
# Opcional: abre audio local silenciado y reproduce cero durante 0,4 s.
conda run -n XMPP python tools/probe_voice_windows.py --audio
```

El sondeo consulta si NVDA está en ejecución con su
[controller client](https://github.com/nvaccess/nvda/blob/master/extras/controllerClient/examples/example_python.py);
no inyecta teclas ni voz. MSAA correcto y NVDA ejecutándose no equivalen a una
sesión manual de navegación y lectura con el lector de pantalla.

## Activación futura y límites

La funcionalidad sólo se instala con `CAN_VOICE_ENABLE=1`. Además requiere
`CAN_VOICE_URL` (WSS con ruta `/voice/v1`), `CAN_VOICE_CERT`, `CAN_VOICE_KEY` y
`CAN_VOICE_SOCKET` en el entorno inicial, antes de importar el binding.
`CAN_VOICE_BIND` tiene valor predeterminado `127.0.0.1`; `CAN_VOICE_PORT`, `8766`.
El archivo de certificados debe corresponder al host publicado. Una configuración
inválida desactiva voz sin impedir que arranque la mensajería. Estos parámetros
documentan el prototipo. La activación de prueba autorizada se registra abajo.

Ejemplo del montaje privado usado en la candidata de producción:

```yaml
environment:
  CAN_VOICE_SOCKET: /var/lib/slidge/voice-runtime/native.sock
tmpfs:
  - /var/lib/slidge/voice-runtime:rw,noexec,nosuid,nodev,size=1m,mode=0700,uid=10000,gid=10000
```

El UID corresponde al usuario `slidge` comprobado en esta imagen. No debe copiarse
a otra imagen sin verificar su usuario. `smoke_voice_activation.py` comprueba con
rutas Linux reales el contrato, permisos, enlaces y rechazo de rutas ocupadas.
`Dockerfile.activation` permite actualizar sólo esa capa Python sobre la imagen
nativa ya validada; el binding se compara por SHA-256 y permanece idéntico.

Captura/reproducción nativa, selección de dispositivos, silencio y controles de
llamada están implementados y comprobados sin cuenta. Quedan la validación manual
completa con teclado/NVDA y una conversación audible de extremo a extremo.
La interoperabilidad
real de señalización, negociación SRTP, pérdida/reordenación/replay, redes distintas,
latencia, calidad de voz y convivencia con el teléfono sólo pueden acreditarse
con pruebas posteriores expresamente autorizadas. El parche HMAC local no equivale
a una auditoría completa del motor. Vídeo y grupos quedan fuera de este prototipo.

## Evidencia del 18 de octubre de 2026

- Suite Python completa tras corregir la entrega de audio: 980 métodos y 474 subcasos
  aprobados, sin omisiones, fallos ni errores (87,524 s). Informe local:
  `.test-results/20261018T181311017949Z-all.json`.
- Entrega irregular sintética: 100 paquetes, cero pérdida simulada, variación de
  40 ms, primera salida a los 60 ms, cero huecos intermedios, PCM exacto y cero
  descartes de reproducción. Informe local: `.test-results/voice-jitter-synthetic.json`.
- Regresiones de audio: reserva y memoria acotadas, captura durante pausa de
  100 ms, contadores de saturación/driver, espaciado de envío, silencio y cierre
  durante la espera. La integración wx/hilo/TLS solicita también el diagnóstico.
- Regresiones de UI: confirmación con **No** predeterminado; destino inmutable,
  cancelación antes de abrir el canal o durante comprobación de audio; cambio de
  perfil, grupos y contactos de otro componente; listener sin captura, entrantes
  silenciadas y Escape. La integración wx/hilo XMPP/TLS/PCM usa el botón del
  contacto y su confirmación, sin el menú ni botones antiguos de habilitación.
- Área bridge tras la corrección: 137 métodos y 71 subcasos aprobados.
- Smokes Linux de activación y binding aprobados localmente y en el VPS, sin red
  ni cuenta; el binario nativo conserva el mismo checksum que las pruebas Go/race.
- Regresiones de voz incluyen silencio durante comprobación del driver, cierre de
  una conexión anterior sin afectar al perfil nuevo y activaciones canceladas por
  desactivación, desconexión o sustitución por otro canal.
- Probe Go: 73 pruebas aprobadas; con detector de carreras: 73.
- Integración Go en las fuentes Slidge v35: 71 aprobadas; con detector de carreras: 71.
  Se ejecutó el paquete raíz con la prueba de hooks reales de `Session` y socket Unix.
- Dos omisiones de MLow declaradas por upstream permanecen identificadas en ambos modos.
- Cadena sintética: dos direcciones, 40 frames, 38 400 muestras, autenticación
  negativa y datos decodificados finitos distintos de silencio.
- Binding gopy reconstruido e importado como usuario `slidge`; smoke ejecutado
  con red deshabilitada, sin montajes ni credenciales.
- Windows/PortAudio real: 18 frames silenciados a 16 kHz mono; ninguna muestra
  guardada ni enviada. Resultado local en `.test-results/voice-windows-native.json`.
- MSAA real: nombres de controles presentes; silencio con rol de casilla y estado
  marcado observable. NVDA estaba ejecutándose. Escape tiene una prueba del handler;
  todavía no se acredita una sesión manual completa de teclado/NVDA.
- Ruff y comprobación de espacios: aprobados.

Imagen local del probe:
`sha256:fd5be8a5282da9c30bbb887ed15b59be59f103d4ba890bfc33873f1521bc1a98`.

Imagen local de Slidge integrada:
`sha256:d276925383c3aa4449cf792465984ba3b3abca14b13fef43f1db8ba4ddaa6e80`.

La huella de las fuentes comprobada contra el probe es
`4d743660ebcc46858089fbae0253f9073b026ceffe1393915dfc4c5afc4bc32d`.
Los IDs se contrastaron con Docker después de terminar las construcciones.
El informe conserva `live_call_verified: false`: acredita las pruebas sin cuenta.
La activación posterior y su imagen derivada se registran abajo; no se publicó en GHCR.

## Punto de continuación

Base de código confirmada: `f92cee014016913873a8d41ee6df5474889cd203` (v35).
La implementación de voz se conserva en el worktree aislado de
`voice-calls`; el checkout original se conserva. La modificación
posterior del VPS está respaldada y documentada abajo.
No se construyó una release Windows, publicó una imagen en un registro ni vinculó
una cuenta nueva. La candidata del VPS reutiliza las sesiones existentes.

La implementación offline del motor, transporte, audio y UI está comprobada.
El usuario reportó una llamada funcional con microcortes el 18 de octubre. Es
evidencia reportada por el usuario; no identifica todavía la causa de los cortes
ni acredita todos los escenarios de llamada entrante/saliente y redes.
Quedan completar la sesión manual de teclado/NVDA y esa matriz de interoperabilidad.
La autorización posterior del usuario permite instalar
la candidata en producción para esas pruebas. El agente no inicia llamadas ni
envía mensajes a contactos. Las comprobaciones de SRTP/replay/reordenación,
calidad y convivencia siguen pendientes; la activación no las acredita.

## Activación de prueba en marco-vps — 18 de octubre de 2026

- Estado previo comprobado: v34, imagen
  `sha256:521db0ca495a56ed1d65fb90e80e91815435570c506114f62c1ee92d811ceec1`.
- Candidata local final: `cliente-xmpp-bridge:v35-voice-trial-20261018-r2`, basada
  en el binding v35 validado. ID de imagen en el VPS:
  `sha256:00b4fc8b28d97f8b165ee4061bee5125452f31203ab9e577ab56395240070a76`.
- El binding nativo conserva el SHA-256
  `d91cdc85085044095e15f8a562fafc823a87844d1cddb24de479f2614e245fde`.
- Helper de voz final: SHA-256
  `c78db84c564aab3bd26326a14ff327edd9f76025ca409138a09d6dad4b40e33e`.
- La imagen nativa se transfirió mediante archivo con checksum verificado;
  la corrección de activación se construyó localmente en el VPS con tres archivos
  públicos de esta rama. No hubo push, tag remoto ni publicación en GHCR.
- Ruta pública: `wss://xmpp.marco-ml.com/voice/v1`. El contenedor publica
  `127.0.0.1:8766`; Nginx verifica también el TLS del upstream, con profundidad 4
  para la cadena vigente, y no registra el acceso a voz. La ruta anterior del portal
  permanece intacta. Comprobación externa: 400 sin subprotocolo y 403 sin token,
  con los cuerpos esperados del servicio de voz.
- Verificación final: 18 de octubre, 17:31 UTC. Contenedor sin reinicios, socket
  Go activo, ruta heredada y directorio/socket privados comprobados. Sin trazas,
  fallos fatales ni errores de permisos detectados desde ese arranque. No se ha
  realizado una llamada ni acreditado todavía audio real con WhatsApp oficial.
- Respaldo privado consistente, configuración previa y manifiesto:
  `/opt/xmpp/backups/voice-trial-20261018T171030Z/`. Se verificaron cinco bases
  SQLite sobre copias, incluyendo WAL; no se escribe en los originales para comprobarlas.
- `rollback.py` en ese directorio restaura imagen y configuración de v34,
  conservando los datos actuales. Rechaza cambios de configuración posteriores
  por checksum. Está preparado y compilado, sin ejecutar tras la activación final.
  Restaurar la copia de datos requiere una decisión aparte para no perder mensajes nuevos.

Para probar los controles nuevos debe reiniciarse el cliente de esta rama aislada,
abrir la conversación y pulsar **Llamar** junto al contacto, seguido de la confirmación.
La instalación habitual del
cliente todavía no incluye estos cambios. Una conexión de control correcta tampoco
sustituye escuchar audio bidireccional durante una llamada real.

## Incidente de apagado del VPS — 18 de octubre de 2026

El journal del arranque anterior registra `Power key pressed short.` en
`systemd-logind` a las 18:16:38 UTC (12:16:38, America/Monterrey), seguido de
`Powering off...` y del cierre ordenado de servicios. El sistema alcanzó
`shutdown.target` a las 18:17:16 UTC. El nuevo arranque comenzó a las 18:25:38 UTC.
Esto acredita una señal de botón de encendido y un apagado del VPS; el journal
consultado no identifica quién originó esa señal en la infraestructura virtual.
No se detectaron OOM ni pánicos del kernel en el tramo consultado desde las
18:05 UTC hasta el apagado. Las actualizaciones no quedan acreditadas como causa.

Tras el arranque, Prosody y la candidata r2 se iniciaron automáticamente a las
18:25:47 UTC, ambos con `OOMKilled=false`, último `ExitCode=0` y sin reinicios
adicionales registrados por Docker. Se comprobó el socket nativo de voz con
propietario 10000 y permisos 0600, acceso externo a SSH/HTTPS/XMPP y las respuestas
400 sin subprotocolo y 403 sin token de la ruta de voz. No se detectaron trazas,
pánicos ni errores de permisos en los logs de ambos contenedores desde ese
arranque. Estas comprobaciones no sustituyen recepción de mensajes ni una llamada
real. No se reiniciaron servicios ni se cambió configuración durante el diagnóstico.

## Diagnóstico de cortes y cadencia Windows — 18 de octubre de 2026

El diagnóstico compartido por el usuario mostró 130 bloques PCM de 20 ms
descartados antes del envío, además de cinco descartes en captura, seis avisos
de entrada y seis de salida del dispositivo. El servicio registró 48 retrasos,
con máximo de 196 ms. Los 112 agotamientos y 37 420 ms sin PCM en reproducción,
así como la pausa máxima de recepción de 797 ms, no distinguen silencios/DTX
remotos de pérdidas o bloqueos del trayecto. No se grabaron muestras de voz.

Se reprodujo otro defecto local sin cuenta, red ni micrófono: en Python 3.12.13
de este Windows, `monotonic` usa `GetTickCount64` con resolución de 15,625 ms.
El envío anterior esperaba 20 ms desde el último envío usando ese reloj y
acumulaba el retraso de cada despertar: 260 bloques tardaron 8 134 ms,
aproximadamente 31,84 bloques/s frente a los 50 necesarios. Este resultado es
de una reproducción aislada; no mide la tasa exacta de la llamada del usuario.

`audio/voice_timing.py` introduce una cadencia con `perf_counter`, plazos
anclados a las muestras y comprobación de despertares anticipados. Después de
perder un bloque completo abandona el plazo anterior para evitar ráfagas de
recuperación. Se aplica tanto al envío como al bombeo de captura/reproducción;
las mediciones de pausas y envío y el reloj de la reserva usan alta resolución.
Mute y revocación se vuelven a comprobar después de esperar, antes del envío.

Una captura sintética independiente de 250 bloques, alimentada desde otro hilo,
atravesó el emisor real a 49,998 bloques/s, con cero descartes. La media entre
envíos fue 20,001 ms; el máximo fue 36,403 ms, por lo que esta corrección no
promete ausencia de jitter del planificador. Evidencia local ignorada:
`.test-results/voice-cadence-fixed.json`. Las regresiones cubren despertares
cuantizados y anticipados, deriva acumulada, pausa larga sin ráfagas, mute y
cierre durante la espera. La comprobación temporal usa reloj preciso y duración
total de la secuencia; un intervalo aislado puede acortarse al compensar un
despertar tardío menor que un bloque.

Validación del cliente: suite completa con 982 métodos y 476 subcasos,
cero fallos/errores/skips, 82,933 s:
`.test-results/20261018T183744238512Z-all.json`; Ruff y `git diff --check`
correctos. La corrección está en las fuentes del worktree y requiere reiniciar
el cliente; no modifica la candidata r2 del VPS. Queda comprobarla en llamada
real y obtener otro diagnóstico después de un corte.

La primera muestra del VPS, 18:34:34–18:35:19 UTC, no mostró descartes UDP,
retransmisiones TCP nuevas ni limitación de CPU del contenedor. Sin embargo,
no coincidió con flujo de voz activo: no permite descartar problemas durante
una llamada. La siguiente medición observa sólo contadores y colas del sistema,
sin guardar paquetes, PCM, contactos ni credenciales. La r2 todavía no expone
contadores de pérdidas RTP, decodificación o descartes internos del motor Go;
los contadores del kernel no sustituyen esa instrumentación.

La segunda muestra, 18:38:19–18:40:21 UTC, tampoco coincidió con sockets UDP
de medios activos. El canal TCP de voz estuvo establecido durante 52 de las 120
muestras de un segundo. No hubo descartes en los sockets observados ni CPU
throttling/steal; el host sí tuvo un pico de CPU ocupada del 90,33 % en una muestra.
Ese pico, sin llamada coincidente, no permite atribuir los cortes al VPS ni
descartar contención bajo llamada. Al terminar, la carga fue 2,35 en ocho CPU.
No se ejecutó captura de paquetes ni se añadió instrumentación al motor en vivo.

## Contadores del puente r3 — 18 de octubre de 2026

El segundo diagnóstico del usuario mostró 8 descartes antes de envío sobre 7089
bloques capturados (0,113 %, frente al 4,105 % de la primera muestra), cero
descartes de captura, un aviso de entrada y otro de salida del dispositivo,
11 retrasos del servicio con máximo 134 ms y envío máximo 6 ms. Continúan
242 agotamientos de reproducción, 76 780 ms sin PCM y pausa de recepción
máxima 517 ms; esos valores todavía no separan silencio, pérdida y demora.
El usuario corrigió la dirección predominante: **CAN hacia WhatsApp oficial**,
aunque también percibe cortes en el sentido contrario.

Se añadió `FlowMetrics` al proxy Python, desactivado por defecto y habilitado
con `CAN_VOICE_METRICS=1`. Durante `active` publica cada diez segundos y al
terminar contadores acumulados de bytes/paquetes PCM recibidos desde CAN,
reenviados al socket nativo, recibidos desde el motor y reenviados al cliente;
incluye máximos de intervalo de llegada, espera de envío y retraso del bucle.
Cada llamada usa una etiqueta aleatoria efímera, sin JID, teléfono, contacto,
identificador real de llamada, token ni muestras de audio. No se registra PCM
ni se capturan paquetes. Los bytes PCM divididos por 32 representan milisegundos.
Un envío al socket nativo acredita la entrega al transporte local, no su
procesamiento posterior por el codificador ni la llegada a WhatsApp oficial.
Las colas y errores internos Go/RTP siguen sin instrumentar.

Validación: integración TLS con PCM ficticio idéntico en ambos sentidos y
contadores de las cuatro fronteras; casos de retraso del bucle, espera de envío,
canal cerrado y reinicio de contadores por llamada. Suite Python completa:
983 métodos, 476 subcasos, cero fallos/errores/skips, 85,407 s, informe
`.test-results/20261018T185304955938Z-all.json`; Ruff y estructura correctos.
La construcción Linux ejecutó los smokes de socket privado, contadores y binding
con red deshabilitada y sin cuenta.

Estado confirmado tras activar esta capa de diagnóstico:

- Imagen local del VPS: `cliente-xmpp-bridge:v35-voice-trial-20261018-r3`.
  ID: `sha256:1095a479b4d3dce127c8ffc2afe79ee9dab2cbbc54539c7c0ca6e45cbb8791c3`.
- Helper: `c2f3f570859aea480bdbac1b0f92ee160da94db063a606ce583fb84d74d25039`.
  El binding nativo conserva el SHA-256 de r2; no se recompiló ni modificó.
- Arranque del puente: 18:55:33 UTC (12:55:33, America/Monterrey).
  Antes de recrearlo se comprobaron cero conexiones de voz y cero sockets UDP
  de medios; Prosody se mantuvo ejecutándose. Socket nativo propietario 10000,
  modo 0600, y respuestas públicas 400/403 esperadas comprobados. Sin reinicios
  adicionales, OOM, trazas, pánicos ni errores de permisos en el arranque.
- Sólo se modificaron imagen y variable de métricas del servicio del puente.
  Contexto, Compose previo/candidato y rollback a r2 preparado y compilado:
  `/opt/xmpp/backups/voice-flow-20261018T1853Z/`. El rollback conserva los datos
  actuales y verifica el checksum antes de restaurar configuración.
  El manifiesto del respaldo original quedó actualizado a r3 para que su
  rollback a v34 siga validando el Compose vigente. Nginx permanece igual.
- `read-flow.py` en ese directorio admite un intervalo acotado, por ejemplo
  `15m`, y devuelve exclusivamente los campos permitidos de las métricas y la
  hora UTC del registro. No expone los demás logs de la aplicación.

No se publicó nada en GHCR. El cliente no necesita otra actualización para
estas métricas. Queda obtener una llamada real posterior a r3 y correlacionar
estos contadores con el diagnóstico local; todavía no se atribuyen los cortes
restantes al motor, a la retransmisión ni a una red concreta.

## Tono local durante llamada saliente — 18 de octubre de 2026

El cliente reproduce un tono intermitente en los estados salientes `initiating`
y `ringing`, después de la confirmación y del comienzo de la llamada. Se detiene
en `connecting`, `active`, `ended`, al colgar/cerrar, ante un error o al desconectar
o cambiar de perfil. Las revisiones y sesiones obsoletas se descartan antes de
tocar el tono; una revisión tardía de `ringing` no lo reactiva tras cancelar.
Las llamadas entrantes conservan sus avisos y preferencias anteriores.

`audio/ringback.py` usa reproducción WAV asíncrona de Windows, sin temporizadores
wx, workers adicionales ni audio enviado al puente. Suena en la salida
predeterminada de Windows. Si falta el recurso o falla el dispositivo, la llamada
continúa sin sustituirlo por un sonido de error del sistema. El sonido local no
se incorpora a las colas PCM ni utiliza el micrófono.

El recurso propio `assets/audio/call-ringback.wav` contiene PCM16 mono a 16 kHz:
un ciclo de tres segundos con 0,9 s de tono 440/480 Hz y el resto en silencio,
rampas de 15 ms y amplitud máxima 0,1. Tanto los datos de paquete de setuptools
como el spec de PyInstaller incluyen WAV además de los MP3 existentes. No se
construyó ni publicó una release.

Las regresiones comprueban el WAV real, reproducción asíncrona e idempotente,
fallo de dispositivo y ocho finales/cancelaciones de llamada. No se inició
una llamada real ni se abrió un micrófono para comprobar este cambio.
Requiere reiniciar el cliente del worktree; no cambia el puente r3.

Validación posterior: suite completa con 985 métodos y 484 subcasos,
cero fallos/errores/skips, 104,266 s, informe
`.test-results/20261018T190801788889Z-all.json`; Ruff, estructura, patrón WAV
del paquete y sintaxis del spec correctos. La reproducción nativa audible y
su volumen percibido quedan para la prueba del usuario; los tests usan un doble
del reproductor Windows y el archivo WAV real, sin emitir el tono durante llamadas.

## Sonidos de conexión y fin de llamada — 18 de octubre de 2026

El usuario confirmó que el tono de espera funciona. Se añadieron dos recursos
propios, locales y asíncronos: `call-connected.wav`, ascendente 660/880 Hz de
260 ms, y `call-ended.wav`, descendente 880/660 Hz de 260 ms. Ambos usan PCM16
mono a 16 kHz, rampas de 10 ms y amplitud máxima 0,1. Los patrones WAV existentes
de setuptools/PyInstaller ya incluyen los nuevos archivos.

El controlador arma estos avisos sólo después de una acción explícita de llamar
o contestar. El primero suena al recibir `active`; el segundo al recibir `ended`,
cancelar una activación pendiente, fallar el inicio saliente o perder el canal
durante ese intento. No se anuncia un fin antes de confirmar un colgado normal.
Los eventos repetidos, revisiones antiguas y sesiones de otro canal no duplican
los sonidos. Una llamada nueva vuelve a armar ambos avisos. Las entrantes que no
se contestan, incluidas las silenciadas, no añaden un aviso de fin a sus
notificaciones anteriores.

Los avisos sustituyen el tono de espera antes de sonar y se reproducen una sola
vez, sin bucle. Continúan usando la salida predeterminada de Windows y no entran
en las colas PCM. Un fallo del reproductor no interrumpe el control de llamada.
Este cambio requiere reiniciar el cliente del worktree; el puente r3 permanece
igual. La audición real de estos dos avisos queda para la prueba del usuario.

Validación posterior: 988 métodos y 492 subcasos en la suite completa,
cero fallos/errores/skips, 100,285 s:
`.test-results/20261018T192044605950Z-all.json`. Ruff, estructura y patrones
de empaquetado de ambos WAV correctos. Se comprobaron llamadas entrantes
contestadas y salientes, duplicados, sesión obsoleta, cancelación, desconexión,
fallo al iniciar y un nuevo intento tras terminar; las pruebas no abren el micrófono.

Por indicación posterior del usuario, el aviso de fin conserva exactamente las
dos notas del de conexión y sólo invierte su orden: 880 Hz y después 660 Hz.
Se copiaron los segmentos PCM originales, manteniendo duración, pausa y volumen.
La regresión del recurso comprueba esa igualdad por muestras; no cambia la lógica
de llamada ni requiere modificar el puente.

## Ráfagas de captura y puente r4 — 18 de octubre de 2026

El usuario volvió a situar los cortes predominantes en CAN hacia WhatsApp
oficial. Su diagnóstico mostró 9408 bloques capturados, cero descartes de
captura/envío, cero avisos del dispositivo, cero callbacks omitidos y cero
retrasos del servicio; espera máxima de envío 3 ms. La recepción registró
54 agotamientos, 12 900 ms sin PCM, 2360 ms descartados y 14 paquetes descartados
antes de reproducir. La pausa máxima de recepción fue 501 ms. Estos contadores
no convierten silencios o demoras en pérdidas de red demostradas.

Se conservó un único registro reciente de 193 575 ms, compatible con la duración
reportada: 9666 paquetes, 6 186 240 bytes tanto recibidos del cliente como
reenviados al socket nativo, unos 49,934 bloques/s. Sin embargo, el proxy midió
pausas de llegada de hasta 348 ms y un retraso propio del bucle de 120 ms.
El envío al nativo esperó como máximo 28 ms. La muestra local y el registro final
del servidor tienen momentos/bases diferentes; no se restan sus bloques para
deducir pérdidas. Evidencia local ignorada, sólo contadores anónimos:
`.test-results/voice-burst/recent-bridge-flow.jsonl`.

La cola nativa de captura de `voicecore` conserva tres bloques y `latest`
descarta el más antiguo cuando se llena. Un ensayo aislado de esa política,
usando la imagen Go validada y sin cuenta/red, introdujo 17 elementos sin
consumir entre ellos: quedaron tres y se descartaron catorce, equivalentes a
280 ms en el formato real de bloques de 20 ms. Informe ignorado:
`.test-results/voice-burst/queue.jsonl`. Este ensayo demuestra la susceptibilidad
a ráfagas; r3 no tenía contadores dentro de Go que acrediten cuántos descartes
ocurrieron efectivamente en la llamada.

`CapturePacer` en el proxy Python añade una reserva inicial de 60 ms y entrega
los bloques al socket nativo cada 20 ms con plazos anclados, sin ráfagas de
recuperación tras un retraso largo. Conserva como máximo veinte bloques en
cola (400 ms de PCM), más el bloque retenido para el próximo envío. Si supera
esa capacidad descarta el más antiguo y cuenta el descarte. Esta reserva puede
aumentar la latencia; no recupera tiempo de red perdido ni garantiza ausencia
de cortes ante pausas prolongadas o lentitud del codificador.

Colgar/rechazar, fin nativo, cambio de llamada, logout y cierre del canal
invalidan también el bloque que espera su turno. Las órdenes de llamada pasan
sin esperar a que se vacíe la cola de audio. No se altera el PCM, el códec ni el
binding Go. Los contadores nuevos distinguen `capture_queue_dropped_packets`,
`capture_revoked_packets` (descarte por terminar/cambiar), `capture_queue_packets`
y `capture_delay_max_ms`.

Pruebas: una ráfaga de dieciocho bloques conserva todas sus muestras y sale
espaciada; límite/descarte de cola, revocación del bloque retenido, otro usuario
no autorizado/cambio de sesión y nueva llamada. La integración TLS conserva
PCM idéntico en ambos sentidos. Suite Python completa: 990 métodos, 495 subcasos,
cero fallos/errores/skips, 105,672 s,
`.test-results/20261019T034142481536Z-all.json`; Ruff y estructura correctos.
Los smokes Linux de ruta privada, contadores, cadencia/revocación y binding
pasaron con red deshabilitada, sin cuenta.

Activación de prueba, sin publicar en GHCR:

- Imagen local: `cliente-xmpp-bridge:v35-voice-trial-20261018-r4`.
  ID: `sha256:681473200bab0de61e0ccd66488fe0d9cd809ca5978bdfc932e5e0a1388ad1d9`.
- Helper: `4ed3ecfda9dba02d97ded6f63eb748f3861f9f61e8f18731ebbe4bb870d40628`.
  Binding Go idéntico a r3: SHA-256
  `d91cdc85085044095e15f8a562fafc823a87844d1cddb24de479f2614e245fde`.
- Inicio del contenedor: 19 de octubre 03:44:35 UTC, equivalente al
  **18 de octubre 21:44:35 en America/Mexico_City**. Se comprobaron cero
  conexiones de voz y cero sockets UDP de medios antes de recrearlo.
  Prosody conservó su arranque anterior; Nginx y las sesiones/datos permanecen.
- Socket privado, helper instalado, respuestas públicas 400/403 y arranque sin
  trazas, pánicos, OOM ni errores de permisos comprobados.
- Configuración previa/candidata, métricas anónimas previas, manifiesto y
  `rollback-to-r3.py` preparado/compilado:
  `/opt/xmpp/backups/voice-capture-20261019T0345Z/`.
  El rollback valida checksum y conserva los datos actuales. El manifiesto del
  respaldo original quedó actualizado a r4.
- El lector vigente es `read-flow.py` de ese directorio. Admite tanto el esquema
  r3 como sus cuatro campos nuevos y excluye los demás logs. Tras activar aún
  no había métricas de una llamada real. No hace falta actualizar el cliente.

Pendiente: probar una llamada real con r4 y comparar calidad, cola, demoras y
descartes. La entrega al socket nativo sigue sin acreditar por sí sola la
codificación/envío de cada bloque ni su recepción en WhatsApp oficial.

### Investigación de implementaciones abiertas (18 de octubre)

Se contrastaron el port fijado de WaCalls, whatsapp-rust, oxidezap/client,
Dino y NetEq. La revisión y las fuentes con revisiones fijadas están en
[investigación de calidad](INVESTIGACION_CALIDAD_LLAMADAS_2026-10-18.md).
Hay una diferencia verificada en el tratamiento de actividad/silencio MLow
respecto al upstream Rust y un caso externo de atenuación al empezar frases;
no se ha demostrado que expliquen nuestros cortes. Prioridades: métricas dentro
de Go, ensayo diferencial de voz–pausa–voz y perturbaciones reproducibles.
Esta investigación no modifica código ejecutable ni producción.

### Primera fase de diagnóstico nativo y corpus de referencia

La siguiente variante aislada añade `voicecore/metrics.go`, observadores en los
puntos de consumo/codificación/RTP y un envío medido por slot anónimo de relay.
El parche se genera contra los anchors del archivo WaCalls verificado; no
cambia los parámetros del códec, el pacing ni las capacidades de las colas.
Los fallos de escritura de diagnósticos no deben frenar los callbacks de audio.
Las marcas temporales internas distinguen pausas reales de una progresión RTP
aparentemente continua. El protocolo de datos y el cliente Windows permanecen
compatibles con esta instrumentación.

El nuevo ejecutable `codecprobe` compara un corpus sintético upstream fijado por
commit/SHA-256: 110 tramas MLow y 105 600 muestras por flujo. Comprueba nuestra
decodificación contra el PCM de referencia y contrasta la actividad de nuestro
codificador con la de referencia sobre el mismo PCM de entrada. Las muestras
se descargan al contexto ignorado; sólo se emiten medidas agregadas. No se
considera que la comparación de marcas de actividad valide por sí sola nuestros
paquetes en un decodificador externo; se informa explícitamente
`outgoing_independent_decode_verified: false`.

Comando reproducible y semántica de todos los contadores:
[README del experimento](../experiments/voice_calls/README.md#instrumentación-y-corpus-de-referencia-18-de-octubre).
La siguiente mejora del códec requiere revisar también la recepción: el port
actual devuelve ceros para cuadros inactivos y no procesa su cuerpo. Antes de
adaptar la codificación de actividad hay que cuantificar esa diferencia con el
corpus y conservar la medición anterior como referencia.

Validación de esta primera fase:

- Python, toda el área `bridge`: 141 métodos, 74 subcasos, sin fallos/errores/skips;
  `.test-results/20261019T041012334068Z-all.json`.
- Go aislado: 80 pruebas normales y 80 con `-race`; binding integrado: 77 y 77
  respectivamente. En cada selección permanecen los dos skips upstream
  declarados anteriormente. Smoke de integración aislada aprobado.
- Ruff completo y `git diff --check` correctos. No es una release del cliente y
  no se repitió su suite completa ya aprobada: los cambios son del experimento
  Go/tooling; se ejecutó toda el área Python del puente y su integración nativa.
- Imagen de ensayo: `cliente-xmpp-voice-probe:v35-metrics-offline`,
  `sha256:fbe54715e9a5b241f4a991ffece52ae4833aa4e4204373a99894f5479d52ffa2`.
- Binding candidato: `cliente-xmpp-bridge:v35-metrics-local`,
  `sha256:c78b3ce45699d315e1418efbc575f28f579d4e3dc80de32ec74569fdd863ba57`.
- Fingerprint de fuentes del ensayo:
  `f7d695d0671016cc3fbd995ec7e017e543f9266e9d34c545837e4e3dfa5f9169`.
- Resumen, manifiesto y evidencia Go conservados en
  `.test-results/voice-metrics-20261019T042236Z/`.

El codificador emitió 110 marcas de voz frente a 96 de referencia; faltan las
3 de actividad residual y 11 de fondo inactivo: 14 diferencias. La comparación
de muestras decodificadas con la referencia dio correlación normalizada a
desfase cero 0,949845 en el flujo completo y 0,289229 en las dos primeras tramas
posteriores a intervalos inactivos. Son medidas del corpus, no una puntuación
perceptual ni una causa demostrada de todos los cortes en llamadas reales.
Detalle de interpretación en
[la investigación](INVESTIGACION_CALIDAD_LLAMADAS_2026-10-18.md#primera-medición-reproducible-del-port-actual).

Pendiente antes de otra activación: adaptar y validar la ruta de inactividad
MLow, tanto su cuerpo decodificado/continuidad como las marcas y cuerpo del
codificador; verificar nuestros paquetes salientes en un decodificador externo.
Antes de correlacionar mediciones reales simultáneas también falta unir las
etiquetas anónimas del proxy y Go y preparar un lector acotado del nuevo esquema.
Después corresponde una llamada comparativa del usuario. Esta fase no accedió
al VPS, no activó imágenes ni publicó en GHCR; producción conserva la última
activación documentada, que no se volvió a consultar aquí.

### Llamada real posterior: descartes locales y del proxy

El usuario confirmó que el diagnóstico posterior correspondía a la llamada
del 18 de octubre, aproximadamente 22:33–22:35 en Ciudad de México. Se consultó
el lector acotado del proxy y únicamente campos seguros del contenedor. La
imagen seguía siendo r4, con inicio `2026-10-19T03:44:35.201753649Z` y cero
reinicios del contenedor; la variante con métricas Go no estaba activada.

El snapshot local mostró 6526 bloques capturados, 6 descartes en captura,
151 antes del envío (3020 ms a 20 ms/bloque), 82 retrasos del servicio de hasta
182 ms y 7 avisos de entrada y 7 de salida. Hubo 118 agotamientos, 32 880 ms
sin PCM, 5700 ms recortados y 84 paquetes retirados antes de reproducir; la
pausa máxima de recepción fue 1630 ms y el await de envío máximo, 8 ms.
Los avisos del dispositivo cuentan desde su apertura; los del transporte,
desde la llamada. No se suman indiscriminadamente ni se interpreta `sin PCM`
como voz perdida o como prueba de pérdida de red.

El registro final del proxy duró 144 028 ms y contabilizó:

- 6979 bloques recibidos del cliente; 6188 entregados al socket Go.
- 770 bloques descartados por saturación de cola: **15 400 ms de PCM**.
- 21 bloques retirados por revocación al terminar: 420 ms, distintos de los
  descartes de saturación. La conservación cierra: 6979 = 6188 + 770 + 21.
- Retraso máximo del bucle Python de 940 ms; espera máxima de un bloque en el
  pacer, 2027 ms. Pausas máximas de recepción: cliente 1625 ms y nativo 1626 ms.
- Await de envío al socket Go máximo 63 ms, al cliente 78 ms. Son duraciones
  locales de escritura, no RTT ni confirmaciones de recepción remota.

El snapshot del cliente se tomó antes del registro final; no se restan sus
6526 bloques de los 6979 del servidor para inferir pérdida. La atribución del
registro se apoya en la confirmación del usuario, no sólo en una duración
parecida.

Una muestra de CPU de dos segundos, posterior a la llamada, dio 86,42 % idle,
0 % steal y 0 % iowait; el contenedor marcó 0,13 % CPU. Esa muestra no describe
la carga durante los cortes ni descarta un pico anterior.

Conclusión acotada: hay descartes reales antes del envío en Windows y en la cola
del proxy, además de la divergencia MLow observada en el corpus. No se atribuye
todo al códec, a Internet o al hardware. Prioridad de diagnóstico: localizar
los bloqueos de los bucles que comparten audio y mensajería y comprobar el
servicio del pacer bajo esas pausas; después evaluar su aislamiento. Aumentar
una cola por sí solo no recupera las muestras ya descartadas ni corrige una
capacidad sostenida de entrega inferior a la captura. No se cambió código ni
se reinició/desplegó el servicio durante esta revisión.

### Aislamiento del transporte PCM: candidato local del 18 de octubre

Implementado en `voice-calls`, después de la llamada anterior.
El WSS público, cliente Unix, pacer y colas de audio viven ahora en un hilo con
su propio bucle asyncio. Slidge conserva autorización, resolución de contactos
y gestión de sesión en su bucle propietario; el transporte sólo conserva
identidades resueltas y señales de revocación thread-safe. No toca wx ni cambia
el transporte del cliente Windows.

Logout y desconexión revocan los permisos antes de esperar al cierre del socket.
Los cambios de sesión/device se comprueban adicionalmente cada 50 ms en el
bucle propietario; esa comprobación puede demorarse si ese mismo bucle se
bloquea. Los permisos consumidos cuya apertura se abandona expiran y se liberan.
Los fallos de bind y la desconexión durante un arranque pendiente limpian el
runner y unen el hilo; un arranque encolado anterior a la desconexión se revoca.
Colgar o finalizar invalida el PCM pendiente antes de resolver un contacto.

Prueba comparativa reproducible: 120 bloques sintéticos únicos, a 20 ms, con
TLS real y eco dúplex en otro hilo. Se bloquea deliberadamente el bucle de
Slidge durante 940 ms mediante `time.sleep`:

| Resultado del último ensayo | Bucle compartido | Transporte aislado |
| --- | ---: | ---: |
| Bloques enviados / recibidos por el backend | 120 / 87 | 120 / 120 |
| Descartes de cola | 33 | 0 |
| Pausa máxima entre respuestas de audio | 958 ms | 32 ms |
| Pausa registrada en el bucle propietario | No instrumentado | 933 ms |

La igualdad de los 120 bloques se comprueba en ambos sentidos. Evidencia en
`.test-results/voice-loop-isolation.json`. Es un ensayo controlado que libera
el GIL durante la pausa: no reproduce todas las causas del incidente. Una
extensión que retenga el GIL, contención de CPU, logging bloqueante, red y
los descartes locales de Windows todavía pueden afectar al audio. No acredita
una llamada real sin cortes ni resuelve las diferencias MLow del corpus.

`CAN_VOICE_METRICS=1` separa ahora `loop_delay_max_ms` (bucle de audio) de
`owner_loop_delay_max_ms` (Slidge). Con `CAN_VOICE_PROFILE=1` se añade un
muestreador optativo mientras hay llamada activa: hasta 16 ubicaciones de código,
con módulo/función/línea y número de muestras. Sólo admite módulos del bridge,
Slidge, asyncio, aiohttp y selectors; nunca lee locales, argumentos o audio.
Sus ubicaciones son indicios de ejecución, no perfiles de CPU ni atribución
causal automática; el GIL también limita el muestreo.

`tools/read_voice_flow.py` está incluido en la imagen en `/opt/voice-tools/`.
Filtra stdin con lista explícita de campos para el proxy antiguo/nuevo y
`CAN_VOICE_NATIVE`, incluidos los contadores anidados. Rechaza campos desconocidos,
identidades, tipos inválidos y ubicaciones fuera del formato; nunca imprime
líneas originales. Limita el escaneo a 16 MiB/10 000 fragmentos, 16 KiB por
línea y los últimos 64 reportes. La entrada debe terminar cada línea con LF.
Debe ejecutarse en el servidor para que sólo salga la salida filtrada, por ejemplo:

```sh
docker logs --since 30m --tail 10000 slidge-whatsapp 2>&1 |
  docker exec -i slidge-whatsapp python -B /opt/voice-tools/read_voice_flow.py
```

Ese comando sólo corresponde a una futura activación que incluya el lector;
no se ejecutó contra producción en esta fase. Las etiquetas anónimas Go y
proxy siguen siendo independientes: no unir llamadas concurrentes por duración.

Validación:

- Suite Python completa antes de añadir el lector: 996 métodos/499 subcasos,
  sin fallos/errores/skips; `.test-results/20261019T045455836575Z-all.json`.
- Tooling y lector después: 10 métodos/13 subcasos, sin fallos/errores/skips;
  `.test-results/20261019T045949115983Z-all.json`. Incluye esquema antiguo/nuevo,
  privacidad, datos inválidos y límites de entrada/salida. No se repitió el resto
  de la suite porque esta adición no cambia código del cliente ni del transporte.
- Smoke Linux en build y runtime: WSS TLS verificado, socket Unix privado,
  eco de PCM sintético, cierre del hilo y permisos; sin cuentas, dispositivos,
  rutas externas, volúmenes personales ni puertos publicados (`--network none`).
- Ruff completo y `git diff --check` correctos. No es una release del cliente.

Imagen local `cliente-xmpp-bridge:v35-voice-isolated-local`:
`sha256:9c34fe2f78ea7d9068ef5c50b77cc8a55837ab9a537427059ba47e6cde155185`.
Deriva del candidato nativo `sha256:c78b3ce45699d315e1418efbc575f28f579d4e3dc80de32ec74569fdd863ba57`;
el SHA-256 del `.so` sigue siendo
`091babc8c87ed9bcfc6a33fc199162fca562559075bd0f43f9df95b3aaabb4f3` en ambos.
Por eso no se recompiló Go ni se repitieron sus pruebas/race ya aprobadas.
Manifiesto de fuentes/evidencia en `.test-results/voice-isolation-build/manifest.json`.

Esta fase no accedió al VPS, no desplegó ni publicó en GHCR. Queda comparar una
llamada real tras una activación controlada con comprobación previa de llamadas
activas y rollback. Después, según los contadores, abordar el bucle de captura
Windows y la divergencia MLow ya documentada; no declarar esos problemas resueltos.

### Activación autorizada de r5 para llamada real

El usuario autorizó después publicar la candidata en producción para probarla.
Se verificó el estado real de r4 y la ausencia de conexiones WSS de voz y UDP
de medios antes de preparar la activación y de nuevo antes de recrear el puente.
El único UDP previo era el DNS loopback del contenedor. No se publicó en GHCR.

- Imagen productiva: `cliente-xmpp-bridge:v35-voice-trial-20261018-r5`.
- ID en el Docker del VPS:
  `sha256:4ea64bf322a1b2f615ac46835756d2135bffd548c6365eb62420a00d7a3f082e`.
  Es el digest de configuración de la misma imagen exportada: el Docker local
  con almacén containerd identifica el índice como `sha256:9c34fe2f78ea7d9068ef5c50b77cc8a55837ab9a537427059ba47e6cde155185`.
  La transferencia se verificó por SHA-256 del archivo y por hashes de helper,
  binding y lector; no se reconstruyó la imagen en el VPS.
- Archivo transferido: 357 676 588 bytes; SHA-256
  `c91f81c31e24e37d4bf9f448d282c8d325c071617acd916826fc9976df4443d6`.
- Inicio: `2026-10-19T05:12:14.118323755Z`, equivalente al
  **18 de octubre a las 23:12:14 en Ciudad de México**. Cero reinicios/OOM.
- Helper: `10c5b0ab36ddcfc0a637e0de857111510ac7c5551b22e26f9c67c3a0c67169c5`.
  Binding: `091babc8c87ed9bcfc6a33fc199162fca562559075bd0f43f9df95b3aaabb4f3`.
- `CAN_VOICE_ENABLE=1`, `CAN_VOICE_METRICS=1`, `CAN_VOICE_PROFILE=1`.
  La comparación del Compose efectivo sólo permitió cambiar imagen y añadir
  el perfilador; se preservaron los volúmenes, credenciales y otros servicios.
- Compose vigente: SHA-256
  `5f406cb8599344003b9c6a7c3a8390cbf187c80d266fe23b4188e5cda55ab68c`.

Respaldo privado en `/opt/xmpp/backups/voice-isolation-20261019T050809Z/`:
Compose anterior/candidato, siete snapshots SQLite mediante la API de backup,
todos con `quick_check=ok`, archivo de imagen, manifiestos y script operativo.
No copiar ese directorio a Git ni usarlo como contexto Docker: incluye datos
y configuración privados. El rollback conserva las bases actuales y valida
el checksum del Compose antes de restaurar la imagen r4:

```sh
python3 /opt/xmpp/backups/voice-isolation-20261019T050809Z/deploy.py rollback
```

Pasaron en el VPS los smokes de binding, TLS, socket Unix y cierre del hilo
con `--network none`, sin cuentas ni volúmenes. Tras activar: listener listo,
socket propietario UID 10000/modo 0600, directorio 0700, respuestas públicas
400 sin protocolo y 403 sin token, siete bases íntegras, cero trazas/pánicos,
errores de permisos o desactivación de voz. Prosody no se reinició. El lector
anónimo dentro de la imagen quedó comprobado; aún no había reportes de llamada.
Estas verificaciones no sustituyen envío/recepción ni una llamada real del usuario.

Se usa el mismo cliente Windows. Próxima comprobación: llamada del usuario de
unos dos minutos, diagnóstico local al terminar y lectura filtrada del intervalo
reciente mediante el comando de la sección anterior. Comparar descartes del
proxy/nativo y pausas de ambos bucles; todavía no se modificó el códec ni el
bucle compartido de captura Windows.
