# Contrato XMPP estructurado de llamadas — puente rayoscompany (v2 de routing)

## Alcance

Esta derivación se despliega en `rayoscompany.com` y se publica experimentalmente como
`ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v26`. La imagen v26 deriva de la variante privada
v2 de routing, conserva las imágenes anteriores como referencia operativa y mantiene el cliente
en una fase experimental.

Conserva el mensaje administrativo textual de Slidge como fallback accesible y añade al mismo
`<message/>` una extensión cuando WhatsApp provee un `CallID`:

```xml
<message type="chat">
  <body>Incoming call from Contact (xmpp:peer@example.invalid) at 2026-08-31 12:34:56+00:00</body>
  <call xmlns="urn:marco-ml:whatsapp:call:1"
        contract-version="1"
        call-id="opaque-call-id"
        peer-jid="opaque-lid@lid"
        chat-jid="contact@whatsapp.example.invalid"
        group-jid="group@g.us"
        direction="incoming"
        kind="voice"
        state="offered"
        event-timestamp="2026-08-31T12:34:56Z"
        sequence="1"/>
</message>
```

Los identificadores del ejemplo están redactados. `peer-jid` y `group-jid` son los JID de
WhatsApp que originó el evento, incluido un posible LID. `chat-jid` es opcional y representa el
JID bare canónico del chat XMPP, derivado por el bridge desde `Actor.JID` mediante su resolución de
contacto; no se extrae del texto de la notificación. El consumidor no debe inferir números ni
convertir esos valores fuera del mapeo canónico del bridge.

## Contrato

- Namespace: `urn:marco-ml:whatsapp:call:1` (no se encontró una extensión vigente del puente
  que representara estos eventos; no se reutiliza Jingle porque no es señalización de llamada).
- Identidad e idempotencia: `call-id + sequence`.
- `sequence`: fase determinista de la fuente, no contador persistido: oferta `1`, aceptación `2`,
  terminal `3`.
- `chat-jid`: ruta canónica opcional del chat XMPP. Cuando está presente, el cliente debe usarla
  como `Message.chat_jid` en live, MAM, inbox y carbons. Cuando falta, un cliente v2 conserva el
  chat de la stanza para que los envelopes antiguos sigan contando globalmente, sin asociarlos a
  un contacto inventado.
- `direction`: `incoming`, `outgoing` o `unknown` cuando el evento no permite probar quién
  inició la llamada.
- `kind`: `voice`, `video` o `unknown`; sólo `CallOfferNotice.Media` aporta el tipo de forma
  verificable en v25.
- `state`: `offered`, `accepted`, `missed`, `rejected`, `ended` o `unknown`.
- `event-timestamp`, `answered-at` y `ended-at` son RFC 3339 UTC si WhatsApp aportó el momento.
  Los atributos ausentes significan `null`/desconocido.
- `terminal-reason` se conserva sólo cuando llega en `CallTerminate`.

La extensión no se emite sin `CallID`: no se sintetiza un identificador con el texto, el contacto
ni el timestamp. En ese caso sigue saliendo exclusivamente el fallback textual, lo que evita
romper la idempotencia.

## Fuente real y límites

La base v25 sólo propagaba `CallOffer` y `CallTerminate`, reducidos a `CallIncoming`/`CallMissed`
y a un timestamp de segundos. La derivación aprovecha los eventos ya expuestos por la versión
vendorizada de WhatsMeow: `CallOffer`, `CallOfferNotice`, `CallAccept`, `CallReject` y
`CallTerminate`.

No hay CDR, duración, audio/video para las ofertas 1:1, ni una garantía de que todos los eventos
salientes lleguen al bridge. Por eso no se calcula duración, no se inventa aceptación ni salida,
y los campos no observables permanecen `unknown` o ausentes.

## Compatibilidad

La extensión se adjunta con `extra_xml` al mensaje de gateway, cuyo cuerpo no queda vacío. Slidge
y Prosody siguen viendo un mensaje normal: MAM, cola offline y notificaciones conservan el fallback
textual. La marca XEP-0203 usa el timestamp UTC del evento cuando está disponible.

Para cruzar los metadatos del evento Go al adaptador Python, la derivación reutiliza exclusivamente
la propiedad `Actor.LID` ya generada y probada por gopy en v25, con el prefijo privado
`call-contract-v1:` y JSON del contrato. El manejador de llamadas ya resuelve el contacto por
`Actor.JID` y no consume `Actor.LID`; Python valida el prefijo y serializa el XML. Así no se añade
ningún getter C/manual ni se entregan a Python punteros de cadena nuevos. El envelope no se emite
ni se persiste fuera del proceso del bridge.

`cliente-xmpp` consume este namespace en las rutas live, MAM, inbox y carbon. Persiste
`call-id + sequence` de forma idempotente, conserva `peer-jid` como dato de WhatsApp y usa sólo el
atributo explícito `chat-jid` para asociar una llamada nueva al chat individual. Los clientes sin
soporte siguen viendo el body como fallback accesible.

## Reproducción y rollback

Construir en rayoscompany desde la variante privada v1 ya desplegada:

La v2 modifica únicamente `session.py`: reutiliza `Actor.LID` y `Actor.JID` ya expuestos por el
binding v1 y serializa el nuevo atributo en Python. No agrega ABI ni getters de gopy, no regenera
C/header y no usa una imagen remota nueva.

```bash
docker build --pull=false \
  --build-arg BASE_IMAGE=cliente-xmpp-bridge:rayos-calls-v1-local \
  -t cliente-xmpp-bridge:rayos-calls-v2-local \
  -f Dockerfile.bridge-calls-routing-v2 .
```

Para publicar la misma imagen ya validada como v26, etiquetarla y subirla desde el host que tiene
Docker y las credenciales de GHCR:

```bash
docker tag cliente-xmpp-bridge:rayos-calls-v2-local \
  ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v26
docker push ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v26
```

Antes de cambiar `compose.yml`, respaldar la composición y confirmar que
`cliente-xmpp-bridge:rayos-calls-v1-local` sigue presente. Para volver atrás, restaurar ese
respaldo o volver a fijar `slidge-whatsapp` a v1 y recrear únicamente ese servicio con
`docker compose up -d --no-deps --force-recreate slidge-whatsapp`. La referencia v25 fijada se
conserva como rollback de segundo nivel.
