# Auditoría del audio saliente y candidata para rayoscompany

## Alcance

Rama `voice-calls`, base `b556b0a`, solicitada para pruebas en
`ssh rayoscompany`. Las activaciones r4/r5 de la guía anterior corresponden a
otro servidor; no se tomaron como estado de este VPS. El preflight comprobó
v35 (`a14c1b58…`) sin voz habilitada. No se inició ninguna llamada ni se
recopiló audio de personas.

## Hallazgos y correcciones

1. **Bloqueos del bucle del proxy.** La rama ya aísla WSS/PCM del bucle de
   mensajería. La regresión ejecutada en esta auditoría bloqueó el propietario
   durante unos 940 ms: transporte compartido, 73/120 bloques y 47 descartes;
   aislado, 120/120 y cero descartes. Máxima pausa del eco: 987 frente a 43 ms.
   Es una reproducción controlada, no la medición de una llamada del usuario.
2. **Actividad MLow incoherente con el estado del detector.** El codificador
   fijaba siempre `0x50`, incluso sobre silencio. Se adaptó el conjunto completo
   de [whatsapp-rust PR #1616](https://github.com/oxidezap/whatsapp-rust/pull/1616):
   decisión previa al hangover, TOC `50/12/10`, símbolos LSF condicionales,
   geometría de pulsos e interpolación cero cuando no se transmite ese índice.
   Cambiar sólo la cabecera no es una reparación válida.
3. **El lector descartaba cuerpos inactivos válidos.** Respondía con ceros sin
   avanzar la síntesis. Ahora distingue SID de un paquete inactivo con cuerpo,
   omite los símbolos que no están en el flujo y conserva el estado entre
   cuadros. No cambia el tamaño de paquete, tasa de muestras ni habilita DTX.
4. **Activación reproducible.** Se añade un entrypoint que crea un directorio
   privado nuevo y hereda `CAN_VOICE_SOCKET` antes de cargar gopy/Go. No borra
   sockets anteriores ni reutiliza su ruta. Slidge continúa siendo propietario
   de la sesión WhatsApp existente.

## Evidencia

Comparación del mismo corpus público fijado de 110 paquetes:

| Medida | Baseline de esta rama | Corregido |
| --- | ---: | ---: |
| Diferencias de actividad respecto a referencia | 14 | 0 |
| TOC de voz / hangover / inactivo | 110 / 0 / 0 | 96 / 3 / 11 |
| Correlación PCM global a desfase cero | 0,949845 | 0,999857 |
| Correlación en primeras tramas tras pausa | 0,289229 | 0,999852 |

La correlación describe coincidencia de muestras del corpus, no una nota
perceptual ni una tasa de pérdida de red. El lector Rust independiente aprobó
los 84 paquetes salientes sintéticos: 252 subtramas, sin ocultaciones/rechazos,
energía pospausa 1,046 veces la previa. El control de cabecera alterada ocultó
34 cuadros y falló como debía. Ver
[receta y límites del lector](../experiments/voice_calls/reference_decoder/README.md).

- Go aislado: 83 pruebas aprobadas normales y 83 con detector de carreras.
  Se conservan los dos skips upstream `TestSmplTablesCDF` y `TestSynth`.
- Python completo: 1002 métodos, 510 subcasos, cero fallos, errores o skips,
  146,298 s; `.test-results/20261020T150248422038Z-all.json`.
- Ruff completo y `git diff --check`: aprobados.
- Evidencia Linux privada: `/opt/xmpp/candidates/voice-audit-20261020/`.
  Contextos de build sólo con fuentes públicas y corpus sintético; sin volúmenes
  productivos ni cuentas en pruebas offline.

La primera comprobación nueva de piso de ruido comenzaba en el cuadro 35,
todavía hangover en este fixture. Se alineó al criterio upstream desde el 36;
no se cambió VAD para forzar una clasificación artificial. También se corrigió
el harness independiente para contar tres subtramas por paquete, no una.

## Límites y prueba del usuario

Las reparaciones eliminan fallos reproducidos del transporte y códec; no
demuestran que sean la única causa de los cortes del interlocutor. Persisten
como factores posibles la captura/planificación de Windows, carga del host,
red y tratamiento remoto. No se sustituyó el motor por otro ni se aumentaron
colas para ocultar descartes.

La prueba debe usar el cliente de esta rama reiniciado, no un instalador antiguo.
Comprobar envío/recepción de mensajes, una llamada en ambos sentidos, pausas
entre frases, silencio, colgar y diagnóstico local al terminar. No confundir
arranque, TLS, estado `active` o escritura al relay con recepción audible.

No se realizó commit/push, publicación GHCR ni build de Windows. El rollback
del VPS debe conservar las bases actuales: los respaldos no se restauran sobre
mensajes nuevos salvo autorización específica.

## Activación verificada en rayoscompany

- Imagen local `cliente-xmpp-bridge:voice-fixed-20261020`, ID
  `sha256:5dfd2e80cd049443b00cb83d693c54d85fe922714eb17fd63c2bb92c2f4c64ea`.
- Inicio definitivo: `2026-10-20T15:19:28.770245646Z`, **20 de octubre,
  09:19:28, Ciudad de México**. Running, cero reinicios adicionales y sin OOM.
- SHA-256 del binding probado y activo:
  `2b2ed35b9d50e6a2b36d3d61e7a6cad87eaea6a9f915daa643c5d7c41efe17fe`.
- Integración nativa: 80 pruebas normales y 80 con race, conservando los dos
  skips upstream. Smokes Linux de binding, TLS, Unix, revocación, cierre del hilo
  y bootstrap aprobados, sin cuenta ni red externa.
- Ruta pública `wss://xmpp.rayoscompany.com/voice/v1`: TLS validado desde Windows,
  400 sin subprotocolo y 403 sin token, incluidos los cuerpos esperados del proxy.
  Sólo el puerto loopback `127.0.0.1:8766` se publica al host.
- Nginx valida también el certificado TLS interno. La clave interna permanece
  UID 10000, modo 0600; no se copió la clave privada del certificado público
  Let's Encrypt al contenedor.
- Socket nativo UID 10000/modo 0600, directorio privado 0700; hash del binding
  activo contrastado con el candidato probado.
- Cuatro bases respaldadas antes y después mediante la API SQLite, todas con
  `quick_check=ok`; las cuentas y tablas duraderas no disminuyeron. Los 27
  contenedores ajenos conservaron ID, imagen y hora de inicio, incluido Prosody.
  Se contrastaron doce campos de identidad/claves del dispositivo: sin cambios.

La cuenta con dispositivo presente en el respaldo completó `Login success`.
Otra referencia de cuenta apunta a un dispositivo que **ya no existía antes**
del cambio y produjo un timeout QR. Se correlacionó localmente, sin exportar
identidades; no se borró ni revinculó esa cuenta fuera del alcance de voz. Esa
excepción no se contó como error del códec ni se ocultó bajo un «cero errores»
global del servicio.

La primera activación detectó un fallo del **script operativo**, no del códec:
`umask 077` convirtió el directorio TLS solicitado como 0750 en 0700, propiedad
de root, e impidió al UID 10000 recorrerlo. El socket Go existía, pero Python
deshabilitó HTTPS. Se restauró v35 con los datos actuales, se reprodujo el
`PermissionError` offline como usuario del servicio, se aplicó `chmod 0750`
explícito al directorio y se volvió a validar la lectura de TLS antes de reactivar.
No se amplió la lectura de la clave privada ni se desactivó la verificación TLS.

Respaldo y manifiesto privados:
`/opt/xmpp/backups/voice-audit-20261020/`. Contexto y operador:
`/opt/xmpp/candidates/voice-audit-20261020/`. Rollback, sólo si procede y sin
restaurar bases sobre mensajes nuevos:

```sh
python3 /opt/xmpp/candidates/voice-audit-20261020/deploy.py rollback
```

Ese operador exige que Compose y el vhost mantengan los checksums de la activación;
si hay cambios posteriores debe detenerse y auditar, no sobrescribirlos. La prueba
audible y de mensajería en el cliente sigue pendiente del usuario, que indicó que
no lo usa por ahora. No se iniciaron llamadas en su nombre.

## Primer intento del usuario: fallo DNS del cliente Windows

Posteriormente el usuario intentó llamar y recibió «No se pudo mantener el canal
de voz seguro. Revisa la conexión del puente» y «Micrófono cerrado». Ese texto
capturaba cualquier excepción del transporte; no demostraba un error de certificado
ni de permisos de micrófono.

Se reprodujo en el mismo Windows/Python `XMPP`, con el perfil remoto y sin CA
personalizada, sin llamadas ni micrófono:

- `socket.getaddrinfo` y el resolvedor de Windows resuelven el host correctamente.
- `aiohttp` elige `AsyncResolver` al estar disponible la dependencia opcional;
  esa ruta falla con `ClientConnectorDNSError` antes de llegar al puente.
- `TCPConnector(resolver=ThreadedResolver())` llega al mismo WSS, verifica TLS y
  obtiene el 403 esperado al no proporcionar un token. Evidencia agregada:
  `.test-results/voice-dns-windows-20261020.json`.

`VoiceTransport` usa ahora el resolvedor del sistema explícitamente. La búsqueda
se ejecuta en el executor de asyncio, no en wx ni en los callbacks PCM. No se
fija una IP, habilita proxy, modifica el DNS global ni relaja TLS/autorización.
Un fallo `gaierror` también se muestra como DNS, sin copiar mensajes libres,
direcciones o tokens a la UI.

Se añadieron dos regresiones a la clase de integración ya registrada: conexión
por **hostname** con TLS y eco PCM aunque falle el resolvedor opcional, y fallo
DNS real que conserva el micrófono cerrado y no filtra el error privado. Los
fixtures anteriores usaban IP de loopback y no cubrían esta frontera. El primer
mock también afectaba al proxy del fixture; se aisló su connector para que la
avería simulada pertenezca sólo al cliente. Las siete pruebas de esta clase
aprobaron, incluidos rechazo de certificado no confiable y redirección.
La suite Python completa posterior aprobó 1004 métodos y 510 subcasos, sin
fallos, errores o skips, en 142,342 s:
`.test-results/20261020T154030042569Z-all.json`. Ruff completo y diff-check
también aprobaron.

Esta corrección está en el cliente de la rama y requiere cerrarlo y abrirlo de
nuevo. No se reconstruyó ni reinició el puente por este incidente, ni se cambió
la vinculación. La apertura autenticada y la escucha real deben confirmarse en
el siguiente intento del usuario; un 403 anónimo sólo verifica DNS/TLS/ruta.
