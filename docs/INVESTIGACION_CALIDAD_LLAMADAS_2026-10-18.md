# Calidad de llamadas: comparación con implementaciones abiertas

Revisión del 18 de octubre de 2026, hora de México. Investigación de código y
fuentes primarias; no modifica el motor, las dependencias ni el despliegue.

## Nivel comprobado

La versión experimental v35 es una **alfa funcional**: ya hay interoperabilidad
real de audio en ambos sentidos con WhatsApp oficial y controles de llamada.
Las pruebas de ciclo de vida, autorización y transporte aportan evidencia sobre
esos límites. No acreditan calidad acústica, latencia ni resistencia a redes
adversas equivalentes a las de un motor VoIP consolidado.

El último diagnóstico comunicado presenta captura y envío locales sin descartes.
Eso no acredita consumo por Go, codificación, envío al relay ni recepción remota.
Los contadores del proxy terminan en la entrega al socket nativo. La causa de
los cortes predominantes de CAN hacia WhatsApp oficial sigue sin demostrarse.

## Cadena realmente integrada

`PortAudio/Windows → PCM16 mono 16 kHz, bloques de 20 ms → WSS/TCP → proxy Python
→ socket Unix → cola Go → MLow, tramas de 60 ms → SRTP → relay de WhatsApp`.

Referencias locales: `cliente_xmpp/audio/live_call.py`,
`cliente_xmpp/xmpp/voice.py`, `tools/bridge_voice.py`,
`experiments/voice_calls/voicecore/{hub,session,engine}.go` y
`experiments/voice_calls/upstream.json`.

`PcmPlayout` aumenta la reserva de 60 a 120 ms tras agotamientos y recorta a
240 ms. No ordena RTP por secuencia/timestamp ni compensa deriva de relojes.
`CapturePacer` regula la entrega al socket; tampoco acredita el consumo posterior.
La captura no integra explícitamente el procesador de audio de WebRTC.

WebSocket se apoya en TCP: sus demoras deben distinguirse de pérdida de
datagramas. La presencia de Pion en el tramo del relay no proporciona por sí
sola NetEq o procesamiento acústico a nuestro cliente Windows.
[RFC 6455, §1.7](https://www.rfc-editor.org/rfc/rfc6455.html#section-1.7),
[arquitectura de los canales de datos](https://www.rfc-editor.org/rfc/rfc8831.html#section-5).

## Fuentes comparadas y aplicación

| Referencia | Evidencia revisada | Aplicación concreta |
| --- | --- | --- |
| WaCalls `edeb31f` | Es también su HEAD consultado; nuestro pin no está atrasado respecto a ese repositorio. Su análisis devuelve TOC `0x50` fijo. | Revisar las correcciones del proyecto Rust del que se portó el códec; actualizar WaCalls a HEAD no las incorpora. |
| whatsapp-rust `d1d929ae` | Análisis MLow con actividad diferenciada; pruebas de voz/silencio y continuidad del decodificador. | Ensayo diferencial del códec y auditoría de los cambios posteriores al port Go. |
| oxidezap/client `0ba4118b` | Cliente WhatsApp con captura nativa, ring buffers, remuestreo y contadores de descarte antes del motor. | Comparar límites entre dispositivo, cola y motor; no copiar capacidades como si fueran latencia objetivo. |
| Dino `8d49d83b` | Cliente XMPP con GStreamer `rtpbin` y procesador WebRTC para eco, ruido y ganancia. | Referencia para separar transporte, reproducción y tratamiento del micrófono. Su protocolo de llamada no sustituye el de WhatsApp. |
| WebRTC NetEq | Búfer adaptativo con decisiones de reproducción y ocultación de pérdida. | Definir los requisitos que nuestra reserva PCM aún no cubre. No puede insertarse sin adaptar sus interfaces y el códec. |

Fuentes por revisión:

- [WaCalls, análisis MLow](https://github.com/JotaDev66/WaCalls/blob/edeb31f0427aba896639db503153b777a405eccf/internal/voip/media/mlow/analysis.go#L238).
- [whatsapp-rust, análisis](https://github.com/oxidezap/whatsapp-rust/blob/d1d929aeb3ea9a10ee61cb87a3176b72e90f3dd2/wacore/src/voip/mlow/analysis.rs#L401)
  y [codificador/pruebas](https://github.com/oxidezap/whatsapp-rust/blob/d1d929aeb3ea9a10ee61cb87a3176b72e90f3dd2/wacore/src/voip/mlow/encode.rs#L804).
- [oxidezap/client, dispositivo](https://github.com/oxidezap/client/blob/0ba4118bcdb02c613fda94c5a0208bff44316e82/crates/audio/src/call_device.rs#L112).
- [Dino, configuración RTP](https://github.com/dino/dino/blob/8d49d83b3b45ab22d7b9d945c4b32296b07cb49e/plugins/rtp/src/plugin.vala#L96)
  y [procesamiento acústico](https://github.com/dino/dino/blob/8d49d83b3b45ab22d7b9d945c4b32296b07cb49e/plugins/rtp/src/voice_processor_native.cpp#L69).
- [NetEq, diseño oficial](https://webrtc.googlesource.com/src/+/main/modules/audio_coding/neteq/g3doc/index.md).

NetEq estima el retardo y decide cómo producir audio ante variaciones y huecos;
también contempla silencio/ruido de confort. GStreamer documenta explícitamente
reordenación, duplicados y paquetes tardíos en
[`rtpjitterbuffer`](https://gstreamer.freedesktop.org/documentation/rtpmanager/rtpjitterbuffer.html).
Son mecanismos distintos de añadir una reserva de bytes PCM.

## Hallazgo prioritario: actividad MLow y comienzos de frase

El [issue #1615](https://github.com/oxidezap/whatsapp-rust/issues/1615) describe
atenuación/pérdida de los primeros 200–400 ms de frases recibidas en Android,
con cadencia regular y RTCP sin pérdidas según las mediciones de su autor.
Es un caso externo, no una reproducción de nuestros cortes.

El [PR #1616](https://github.com/oxidezap/whatsapp-rust/pull/1616), integrado el
3 de octubre, diferencia voz (`0x50`), actividad residual (`0x12`) y fondo
inactivo (`0x10`). Cambia también la geometría de pulsos y los símbolos LSF
del cuerpo; cambiar únicamente el byte TOC produciría una trama incoherente.
El autor aporta pruebas del decodificador de referencia, pero declara pendiente
la comprobación del síntoma en una llamada real. El cierre del issue no prueba
por sí solo esa resolución acústica.

Nuestro port Go conserva TOC constante, por lo que existe una diferencia
verificada que merece un ensayo. Todavía no sabemos si los cortes del usuario
se concentran al reanudar el habla; no se atribuyen a este hallazgo sin medirlo.

La [especificación comunitaria de whatsmeow-voip](https://github.com/fenleon/whatsmeow-voip/blob/120ffbc910bdcc423aad424394dd44884e33ca8d/docs/whatsapp-calls-protocol-spec.md)
ayuda a contrastar formatos y criptografía. Su
[README](https://github.com/fenleon/whatsmeow-voip/tree/120ffbc910bdcc423aad424394dd44884e33ca8d)
describe una implementación por etapas: no se trata como sustituto completo y
maduro. Comparte referencias con nuestro códec; no constituye una validación
independiente de toda la cadena.

## Trabajo siguiente y criterio de verificación

1. **Cerrar la observabilidad dentro de Go.** Contar bloques aceptados,
   descartes de cola, muestras consumidas, tramas codificadas/fallidas,
   duración de codificación, secuencia/timestamps y resultado de envío por
   relay. Exponer resúmenes RTCP cuando estén disponibles. Entrega local al
   socket, envío al relay y recepción informada por el remoto son hechos
   distintos. Mantener métricas agregadas sin audio, JID ni claves.
2. **Ensayo diferencial MLow.** Señales sintéticas voz–pausa–voz, silencio y
   habla continua; comparar el port con una referencia fijada, incluyendo
   cabecera, cuerpo, continuidad y energía al reanudar. Si se adapta #1616,
   hacerlo completo en rama y validar contra referencia, no sólo con nuestro
   propio decodificador.
3. **Banco de perturbaciones reproducible.** Incluir ráfagas, pausas de
   transporte, consumidor lento, pérdida/reordenación en el tramo de
   datagramas, deriva y dúplex. Medir discontinuidades y latencia además de
   contadores. No confundir el ensayo de colas con una prueba acústica real.
4. **Revisar el motor de medios con esas mediciones.** Tratamiento temporal
   por secuencia/timestamp, política de paquetes tardíos, ocultación de
   pérdidas y procesamiento acústico. Evaluar WebRTC/GStreamer para el tramo
   cliente–puente si la evidencia justifica el cambio; no prometer que cambiar
   TCP por UDP elimina cualquier corte. Mantener autorización, dispositivos,
   mute y revocación inmediata al terminar.

Esta revisión deja prioridades concretas; no selecciona todavía una migración
de motor ni certifica la causa. Las próximas comparaciones deben cambiar una
variable y conservar una referencia anterior para medir la diferencia.

## Primera medición reproducible del port actual

La instrumentación y el ensayo están implementados en la rama aislada. El
manifiesto `experiments/voice_calls/codec-reference.json` fija por commit,
longitud y SHA-256 tres archivos sintéticos públicos. Su
[procedencia upstream](https://github.com/oxidezap/whatsapp-rust/blob/d1d929aeb3ea9a10ee61cb87a3176b72e90f3dd2/wacore/src/voip/mlow/testdata/PROVENANCE.md)
identifica los paquetes y PCM como derivados del códec distribuido de WhatsApp
y declara que no contienen llamadas grabadas ni información personal.

Resultado sobre 110 tramas de 60 ms, 105 600 muestras por flujo:

| Medición | Port actual | Referencia |
| --- | --- | --- |
| Marcas de voz `0x50` al codificar la misma entrada | 110 | 96 |
| Actividad residual `0x12` | 0 | 3 |
| Fondo inactivo `0x10` | 0 | 11 |

Hay **14 diferencias de actividad**. Esto acredita una diferencia concreta del
codificador; no demuestra por sí solo el comportamiento de su receptor remoto.

Al alimentar los paquetes de referencia a nuestro decodificador, la correlación
normalizada a desfase cero con el PCM de referencia es 0,949845 y el error RMS
normalizado es 0,027565. En las dos primeras tramas posteriores a cuadros
inactivos (1920 muestras en total), los valores son 0,289229 y 0,055682.
Estos números comparan muestras alineadas del corpus; no son una puntuación
perceptual de llamada ni una medición de pérdida de red.

El port devuelve ceros para cuadros inactivos en `mlow/decoder.go` en lugar de
procesar el cuerpo. Debe revisarse esa ruta y la continuidad de sus estados,
además de la codificación de actividad. La medición localiza una divergencia
reproducible especialmente visible al reanudar la señal; todavía no separa qué
parte corresponde a cada diferencia de implementación del códec.

El ensayo aún no ejecuta nuestros paquetes salientes en el decodificador
original (`outgoing_independent_decode_verified: false`). Tampoco acredita
calidad en una llamada real (`live_call_verified: false`). El JSON completo se
guarda en `.test-results/voice-call-probe/codec-reference.json`.
