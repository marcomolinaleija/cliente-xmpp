# Llamadas entrantes y duración disponible — v33 publicada

El candidato conserva los registros tipados de llamadas que llegan dentro del
historial de chats individuales. Antes se descartaban al aplicar el filtro de
historial grupal, perdiendo su duración explícita. También corrige la dirección
de la señalización entrante y solicita el registro inicial tras autenticar la
conexión, incluida una reconexión real.

La prueba real posterior mostró una llamada entrante con oferta y aceptación,
pero sin registro final ni duración. Por ello el segundo candidato añade
recuperación acotada y diagnóstico. La segunda prueba real, contestada en móvil,
confirmó el tipo de voz, pero siguió sin duración: llegó `accepted_elsewhere` y
tres consultas exitosas no aportaron un registro final. **La duración en ese
escenario sigue siendo una limitación del enlace actual**, no una corrección
completada. Las pruebas sintéticas no demuestran que WhatsApp envíe ese registro.

Tras aceptar este límite, el usuario autorizó publicar v33 el 12 de octubre de 2026.
La imagen publicada y activa en el VPS es:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v33@sha256:3f588f68247239a08c991c08eeb8195ff361cd887053a9c046fb60946ae555fa
```

## Prueba manual

1. Usar CAN conectado al puente remoto de prueba; no hace falta recompilar el cliente.
2. Recibir una llamada nueva, contestarla en WhatsApp oficial y terminarla.
3. Comprobar en CAN la dirección entrante, una sola llamada y su duración cuando
   WhatsApp envíe el registro final. Revisar estadísticas globales, diarias y del chat.
   Para una llamada breve, esperar hasta tres minutos y volver a abrir las estadísticas.
   Si falta la duración, informar dónde se contestó (celular o Desktop/Web) y cuándo terminó.
4. Repetir una llamada entrante perdida y una saliente contestada: no convertir
   la espera de la perdida en tiempo conversado ni degradar las salientes.
5. Comprobar que el historial enriquecido no anuncia llamadas nuevas. Info ya fue
   validada por el usuario y no se modifica en este ajuste.

No se necesita cerrar la sesión de WhatsApp, borrar datos ni volver a vincular
el teléfono para esta prueba. La publicación no garantiza tiempos de todas las llamadas.

## Qué se corrige

- `newEventFromHistory` detecta `CallLogMessage` antes del filtro que sólo permite
  mensajes normales de grupos. Usa el parser de WhatsMeow para conservar IDs,
  dirección, remitente y wrappers `DeviceSentMessage`; otros mensajes individuales
  siguen sin entrar por esta ruta grupal.
- `DurationSecs` se transporta como segundos explícitos y `source=history_sync`.
  El contrato v1 y el binding existente no cambian. Los avisos de aceptación,
  registro final y duplicados se fusionan por cuenta e ID estructural de llamada.
- Un creador remoto válido se clasifica como entrante. Si la aceptación llega
  desde el teléfono propio, se conserva el creador remoto como peer, no el propio
  usuario. PN/LID propios y metadatos incompletos siguen diferenciados.
- El snapshot de `WAPatchRegular` se solicita en `events.Connected`, no antes de
  autenticar. Se conserva el procesamiento de `AppState` y `HistorySync.callLogRecords`.
- Una oferta con nodo `video` se clasifica como vídeo, incluso si también incluye
  `audio`; sólo `audio` indica voz. Una oferta sin esos datos permanece desconocida.
- Tras una aceptación entrante se consulta `WAPatchRegular` a los 15, 60, 180,
  600 y 1800 segundos: snapshot en el primer y último intento, deltas en el resto.
  Son como máximo cinco consultas, con timeout de 20 segundos cada una y cuatro
  llamadas pendientes por sesión. Señales duplicadas no reinician el calendario.
  Un registro final suficiente cancela la recuperación; `ongoing`, `upcoming` o
  aceptación en otro dispositivo sin duración no la cancelan. Cerrar la sesión
  cancela sus workers; una desconexión detiene las consultas. No hay polling infinito.
- Los logs `CAN_CALL` contienen hash del ID, fase, enums y presencia de duración.
  No contienen nombres, JIDs, ID nativo, cuerpos, tokens ni nodos crudos. Permiten
  diferenciar señalización, registros emitidos y resultado de cada consulta sin
  activar debug global. La consulta no marca chats leídos ni modifica llamadas.

Las estadísticas del cliente ya suman ambas direcciones si hay datos suficientes.
No se cambia su fórmula ni se incluyen conversaciones administrativas del componente.

## Límite de los datos antiguos

`accepted_elsewhere` confirma aceptación en otro dispositivo, **no el final**.
Sin duración explícita ni horas fiables de aceptación y fin no se inventa tiempo.
Los registros antiguos incompletos sólo se enriquecen si WhatsApp aporta el
registro correspondiente; este parche no garantiza recuperar todos los históricos.
No se deduplican llamadas por texto, cercanía temporal o nombre del contacto.

## Imagen y rollback

`tools/Dockerfile.bridge-call-duration-v33` parte del candidato local de Info y
lectura grupal. La receta completa reproducible desde v32 sigue siendo
`tools/Dockerfile.bridge-message-info-v33`, que incorpora también el parche de recuperación.
Ambas ejecutan Go, race, compilación nativa y smokes del contrato y funciones previas.

El segundo tag de prueba es `cliente-xmpp-bridge:call-recovery-v33-candidate`. El override
sólo reemplaza la imagen de `slidge-whatsapp`; Compose base, cuentas, volúmenes,
Prosody y adjuntos se conservan. El candidato anterior sigue disponible:

```sh
cd /opt/xmpp
docker compose -f compose.yml \
  -f call-duration-candidate-20261012/candidate.override.yml \
  up -d --no-deps slidge-whatsapp
```

No restaurar bases antiguas como rollback de imagen: se perderían mensajes y
sesiones posteriores al respaldo. El Compose base usa ahora la referencia pública
v33 por digest; los overrides de candidatos se conservan para reversión explícita.
