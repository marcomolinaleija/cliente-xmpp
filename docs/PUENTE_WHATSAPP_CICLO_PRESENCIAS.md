# Ciclo de vida seguro para la renovación de presencias de WhatsApp

## Estado

El 9 de agosto de 2026 se construyó, publicó y desplegó en `marco-vps`:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v14
sha256:3efeae0eb471bf131fc6af388569ecbd052c14012f6fb963e043a2d1b0760f8f
```

El contenedor quedó activo con cero reinicios, recuperó las dos sesiones y completó sus dos
sincronizaciones automáticas de roster. El respaldo anterior está en
`/opt/xmpp/backups/presence-lifecycle-v14-20260809-141226/`.

## Incidente

Desde el despliegue de `v13`, Docker registró reinicios periódicos con este stack estable:

```text
panic: runtime error: invalid memory address or nil pointer dereference
codeberg.org/slidge/slidge-whatsapp/slidge_whatsapp.(*Session).SubscribeToPresences
    /src/slidge_whatsapp/session.go:550
codeberg.org/slidge/slidge-whatsapp/slidge_whatsapp.(*Session).Login.func1
    /src/slidge_whatsapp/session.go:116
```

No fueron reinicios por falta de memoria. El temporizador interno usa un intervalo nominal de 12
horas con jitter de ±6 horas, que coincide con la separación observada entre los panics.

## Causa

La goroutine de renovación leía `s.client` al vencer el temporizador. Las rutas `Disconnect`,
`Logout` y `events.LoggedOut` podían limpiar ese puntero mientras el timer ya estaba listo. Cerrar
el canal de cambios de presencia no eliminaba la carrera porque un `select` puede escoger el timer
cuando ambos casos están listos.

La ruta `events.LoggedOut` era especialmente incompleta: asignaba `s.client = nil` sin cancelar el
contexto ni detener el renovador. El mismo dispatcher seguía leyendo `s.client` en otros eventos,
por lo que una limpieza concurrente podía abrir una segunda desreferencia nula.

## Corrección de v14

`tools/patch_slidge_whatsapp_presence_lifecycle.py` aplica estas invariantes sobre el código exacto
incluido en `v13`:

1. El acceso al puntero del cliente en las rutas afectadas usa un `sync.RWMutex` y devuelve una
   instantánea local.
2. Cada renovador captura el cliente y el contexto de su propio login; no vuelve a consultar el
   puntero mutable al vencer el timer.
3. El renovador tiene cancelación, canal `done` y teardown idempotente. La limpieza cancela y espera
   la goroutine antes de continuar.
4. El canal de presencia ya no se cierra. La actualización conserva sólo el estado más reciente y
   no puede provocar `send on closed channel` ni un cierre doble.
5. `SubscribeToPresences` tolera cliente, store o almacén de contactos ausentes y usa la instantánea
   recibida durante toda la operación.
6. `handleEvent` captura una sola instantánea del cliente y descarta eventos que llegan después de
   terminar la sesión.
7. La renovación periódica se conserva; no se desactiva la función que evita que WhatsApp deje de
   entregar presencias.

El parche es idempotente y falla si los bloques esperados no aparecen exactamente una vez. Esto
evita aplicar silenciosamente la corrección sobre una versión de upstream incompatible.

## Construcción

El Dockerfile fija el digest de `v13`, aplica el parche, incorpora la prueba Go y recompila el
binding CGO:

```bash
docker build \
  -f tools/Dockerfile.bridge-presence-lifecycle-v14 \
  -t ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v14 \
  tools
```

No usar como contexto todo el repositorio si se copian archivos manualmente a la VPS. El contexto
temporal sólo necesita el Dockerfile, el parche, la prueba Go, el smoke Python y
`gopy_whatsapp_exports.map`.

## Pruebas

El build ejecuta obligatoriamente:

```bash
gofmt -w session.go presence_lifecycle_test.go
go test .
go test -race .
```

`tools/bridge_presence_lifecycle_test.go` cubre:

- La ausencia de cliente durante teardown.
- La detención repetida e idempotente del renovador.
- Renovación y limpieza concurrentes durante 500 iteraciones.

Después del enlace se ejecuta `tools/smoke_bridge_presence_lifecycle_runtime.py`. También deben
pasar los trece smoke tests heredados enumerados en `PUENTE_PERSONALIZADO.md`; en la imagen
publicada el 9 de agosto pasaron los catorce.

## Publicación y despliegue realizados

Se completaron estos pasos:

1. Se publicó `v14` en GHCR y se registró su digest inmutable.
2. Se respaldaron `compose.yml`, `/opt/xmpp/slidge` y la configuración de Prosody con el puente
   detenido.
3. Se cambió únicamente la imagen de `slidge-whatsapp` y `docker compose config -q` validó el
   archivo.
4. Se recreó únicamente el puente.
5. Se confirmaron dos autenticaciones, dos logins y dos sincronizaciones de roster.
6. No aparecieron fallos de roster, privilegios, panics ni segfaults. El único traceback fue el
   timeout esperado de una sesión administrativa donde no se escaneó un QR; las dos sesiones
   vinculadas permanecieron conectadas.

El periodo de observación debe superar 18 horas para cubrir el máximo del primer timer con jitter.
Conviene mantenerlo al menos 36 horas para atravesar más de una renovación. El nuevo contenedor
comenzó el 9 de agosto de 2026 a las 20:12:43 UTC con `RestartCount=0`; la observación posterior
debe comparar contra ese inicio y no contra los 13 reinicios históricos de `v13`.

No ejecutar `docker compose down -v`, no borrar `/opt/xmpp/slidge` ni los adjuntos y detenerse si
alguna cuenta solicita vinculación por QR después del cambio.
