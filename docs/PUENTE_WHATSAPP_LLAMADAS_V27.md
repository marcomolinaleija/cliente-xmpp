# Llamadas completas de WhatsApp — v27 publicada

## Estado

`v27` fue validada como candidata local y publicada en GHCR el 2 de septiembre de 2026. Desde esa
fecha está activa temporalmente en el servicio productivo para la prueba controlada solicitada por
el propietario. La referencia publicada e inmutable es:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v27@sha256:b292de2e52cb7ceed08ab0f5784a47eaad1362e4cc0040c2bbdc13bd6cea561c
```

El manifiesto estable para el puente local ya apunta a esa referencia. El Compose de `marco-vps`
conserva deliberadamente la etiqueta local validada, para no sustituir la instancia en producción
sin una prueba o rollback explícitos.

Base inmutable:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v26@sha256:db4d27890c83a24d167e7e4f7d559f4ae7313fe9e14033c8219094aac74996be
```

Etiqueta de prueba en `marco-vps`:

```text
cliente-xmpp-bridge:v27-callfix2
```

ID local validado por el build reproducible del 2 de septiembre de 2026:

```text
sha256:e3ea425c70d35a046562ae8c60ce45060a4eb8f6713fc35b06f29f6f731172c3
```

El Compose productivo apunta temporalmente a esa etiqueta local. El contenedor inició con el ID
esperado, sin reinicios, errores ni fallos fatales; el marcador de arranque de Slidge y
`PRAGMA quick_check` de `slidge.sqlite` resultaron correctos. La imagen v26 permanece en el host
como rollback.

La corrección `callfix` trata `CallTerminate(reason=accepted_elsewhere)` como aceptación en otro
dispositivo (secuencia 2), no como fin. Al iniciar una sesión vinculada fuerza además un snapshot
de `appstate.WAPatchRegular` con eventos habilitados; ahí vive el índice `call_log` que aporta el
resultado final y la duración.

Respaldos previos a la activación:

```text
/opt/xmpp/backups/compose-pre-v27-test-20260902-132457.yml
SHA-256 482aab8b24443f4a3a0d0163f276ff3f02a9f87d1eb86381c52a1acaed9b3d34

/opt/xmpp/backups/slidge-pre-v27-test-20260902-132457.tar.zst
SHA-256 d697c4329a35d8e0266952e8af192e4185417d84800b73c65b94807b9b12b36e

Para el cambio `callfix` también quedaron respaldos de Compose antes de cada recreación:

```text
/opt/xmpp/backups/compose-pre-v27-callfix-20260903-021833.yml
/opt/xmpp/backups/compose-pre-v27-callfix-image-20260903-021913.yml
/opt/xmpp/backups/compose-pre-v27-callfix2-20260903-023444.yml
```

## Qué cambia

La versión vendorizada de WhatsMeow ya contiene tres fuentes complementarias de datos de
llamadas:

1. Eventos de señalización (`CallOffer`, `CallAccept`, `CallReject`, `CallTerminate`), ya usados
   por v26. Son útiles para avisos inmediatos, pero no constituyen un registro final confiable.
2. `waSyncAction.CallLogRecord`, recibido en `HistorySync.callLogRecords` y en
   `events.AppState`. Es la fuente autoritativa: incluye dirección, duración, hora inicial,
   voz/vídeo, resultado, identificador, creador, grupo y participantes.
3. `waE2E.CallLogMessage`, que puede llegar como mensaje en tiempo real o como respaldo. Incluye
   duración explícita en segundos y resultados adicionales para llamadas silenciadas.

El parche v27:

- activa `SupportCallLogHistory` en el payload de registro de WhatsMeow;
- consume registros históricos de `HistorySync` sin generar una notificación de llamada nueva;
- consume cambios de `AppState` y `CallLogMessage` en tiempo real;
- elige el participante remoto en llamadas salientes y conserva el JID del grupo por separado;
- emite la fase autoritativa con `sequence="4"`, para que sustituya el resumen de señalización;
- conserva `duration-seconds`, `outcome` y `source` en el mismo namespace v1 compatible con v26.

Ejemplo:

```xml
<call xmlns="urn:marco-ml:whatsapp:call:1"
      contract-version="1"
      call-id="opaque-call-id"
      peer-jid="contact@example.org"
      chat-jid="contact@whatsapp.example.invalid"
      direction="outgoing"
      kind="video"
      state="ended"
      event-timestamp="2026-08-31T12:34:56Z"
      sequence="4"
      duration-seconds="73"
      outcome="connected"
      source="app_state"/>
```

Resultados preservados desde `CallLogRecord`: `connected`, `rejected`, `cancelled`,
`accepted_elsewhere`, `missed`, `invalid`, `unavailable`, `upcoming`, `failed`, `abandoned` y
`ongoing`. `CallLogMessage` añade `silenced_by_dnd` y `silenced_unknown_caller`; el cliente los
cuenta como llamadas perdidas sin borrar la causa.

## Cliente

SQLite agrega tres columnas compatibles hacia adelante: duración explícita, resultado y fuente.
El registro autoritativo se fusiona por `account_jid + call_id + sequence`, conserva la actualización
de `AppState` frente a un `HistorySync` tardío y evita volver una llamada terminada al estado
`ongoing`. Las estadísticas separan contestadas, perdidas, rechazadas, canceladas, no disponibles,
fallidas, en curso/programadas, entrantes, salientes, voz y vídeo. El tiempo total y la duración
habitual prefieren la duración explícita de WhatsApp y mantienen el cálculo antiguo sólo como
compatibilidad.

## Construcción reproducible

El contexto debe contener el parche, las pruebas y el mapa de símbolos que referencia el
Dockerfile:

```bash
  docker build --progress=plain \
  -f Dockerfile.bridge-calls-v27 \
  -t cliente-xmpp-bridge:v27-callfix2 .
```

El build ejecuta `go test .`, `go test -race .`, recompila el binding compartido de gopy y ejecuta
las pruebas de contrato/runtime en Python. No contiene credenciales ni requiere cambiar Compose.

Archivos reproducibles:

- `tools/patch_slidge_whatsapp_call_records_v27.py`
- `tools/Dockerfile.bridge-calls-v27`
- `tools/bridge_call_records_v27_test.go`
- `tools/bridge_calls_v27_contract_test.py`
- `tools/smoke_bridge_calls_v27_runtime.py`

## Límite del historial y vinculación

`SupportCallLogHistory` forma parte de `DeviceProps`, y WhatsMeow sólo incluye `DeviceProps` en el
payload de registro de un dispositivo nuevo. Al reconectar una sesión ya vinculada usa el payload
de login, que no renegocia esa capacidad. Por tanto:

- la prueba en una sesión existente puede confirmar `AppState`/`CallLogMessage` y llamadas nuevas;
- para demostrar el backfill completo de `HistorySync.callLogRecords` hace falta una cuenta de
  prueba o volver a vincular conscientemente el dispositivo;
- no se debe cerrar sesión ni eliminar el dispositivo productivo sólo para probar este cambio.

## Prueba real posterior a la publicación

Durante la prueba productiva controlada, conservar v26 como rollback. Registrar en los logs el XML
redactado y comprobar en el cliente:

1. llamada entrante perdida;
2. llamada entrante contestada y terminada;
3. llamada saliente cancelada antes de contestar;
4. llamada saliente contestada durante al menos 15 segundos;
5. una videollamada y, si es posible, una llamada grupal;
6. reinicio del bridge y del cliente, verificando que las mismas llamadas reaparezcan una sola vez;
7. duración, dirección, tipo, resultado y conversación correctos en estadísticas globales, diarias
   y por chat;
8. ningún anuncio/sonido nuevo por registros recibidos desde historial.

Mantener la imagen local y sus respaldos hasta confirmar que la unidad de
`CallLogRecord.duration` coincide con los segundos de `CallLogMessage.durationSecs`, que el
`call-id` de ambas fuentes converge y que cada registro se asocia al contacto/grupo correcto. Esos
tres puntos sólo pueden cerrarse con datos reales redactados; las pruebas sintéticas no deben
convertirlos en una suposición de producción.

## Evidencia observada en la caché local

La caché contiene 116 registros `sequence=4` de origen `app_state`: 81 conectados, 28 perdidos,
6 no disponibles y 1 rechazado. Hay duración explícita tanto en llamadas salientes (por ejemplo,
2763 segundos) como en una llamada entrante conectada (31 segundos), con dirección, contacto y
tipo conservados. El caso `accepted_elsewhere` fue corregido en `v27-callfix2`; queda repetir ese
escenario concreto después del despliegue para confirmar su convergencia en vivo.
