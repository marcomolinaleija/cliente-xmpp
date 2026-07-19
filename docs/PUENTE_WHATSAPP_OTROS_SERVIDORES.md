# Instalar el puente de WhatsApp en otros servidores XMPP

## Objetivo

Esta guía describe cómo desplegar la imagen personalizada vigente de `slidge-whatsapp` en una
instalación distinta de `marco-vps` y concederle los privilegios XEP-0356 necesarios. Esos
privilegios permiten:

- Sincronizar automáticamente los contactos de WhatsApp con el roster XMPP.
- Reflejar en XMPP los mensajes y acciones realizados desde la aplicación oficial de WhatsApp.
- Sincronizar marcadores de lectura en chats individuales y grupos.
- Evitar los errores `Automatic XMPP roster sync failed ... IqTimeout` e
  `IQ privileges not granted` cuando el servidor sí admite estas funciones.

La imagen vigente es:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v10
sha256:3fc9221ce5da3bf9dffd807e72b52b09ef1f4c84fce2fff077dfe70702c56b73
```

## Requisito fundamental

XEP-0356 sólo funciona cuando las cuentas XMPP y el componente de WhatsApp pertenecen al mismo
servidor XMPP. Por ejemplo, `usuario@example.org` y `whatsapp.example.org` deben estar
administrados por la misma instalación de Prosody o ejabberd.

Si el componente está alojado en un servidor XMPP externo, no se debe activar
`SLIDGE_WHATSAPP_ALWAYS_SYNC_ROSTER`. Los mensajes básicos pueden funcionar, pero no estarán
disponibles la sincronización automática del roster ni las demás acciones privilegiadas.

## Datos que deben identificarse antes de cambiar nada

Para cada instalación se necesitan estos valores:

| Dato | Ejemplo |
| --- | --- |
| Dominio de usuarios XMPP | `xmpp.example.org` |
| Dominio del componente | `whatsapp.xmpp.example.org` |
| Servicio del servidor XMPP en Compose | `prosody` o `ejabberd` |
| Servicio del puente en Compose | `slidge-whatsapp` |
| Ruta de configuración | `/opt/xmpp/prosody/config/prosody.cfg.lua` |
| Ruta persistente de Slidge | `/opt/xmpp/slidge` |

No se debe copiar el dominio ni el secreto de `marco-vps`. Cada servidor conserva sus propios
dominios, certificados y `component_secret`.

## Respaldo previo

Antes de editar, respaldar como mínimo el archivo Compose, la configuración del servidor XMPP y
los datos persistentes de Slidge. Un ejemplo para una instalación bajo `/opt/xmpp` es:

```bash
cd /opt/xmpp
stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p "backups/bridge-$stamp"
cp -p compose.yml "backups/bridge-$stamp/compose.yml"
cp -a prosody/config "backups/bridge-$stamp/prosody-config"
docker compose stop slidge-whatsapp
cp -a slidge "backups/bridge-$stamp/slidge"
docker compose start slidge-whatsapp
```

Detener brevemente el puente evita copiar `slidge.sqlite` mientras está cambiando. Si el
despliegue se hará inmediatamente, puede mantenerse detenido después del respaldo y recrearse en
el paso correspondiente. Adaptar las rutas a la instalación real. No ejecutar
`docker compose down -v`, no borrar volúmenes y no eliminar la base `slidge.sqlite`: allí vive la
vinculación con WhatsApp.

## Configuración para Prosody

### Versión y módulo

Usar `prosodyim/prosody:0.12`. La antigua imagen 0.11.9 usada en algunas instalaciones no
entregaba correctamente los IQ PubSub privilegiados requeridos por XEP-0490.

El módulo comunitario `mod_privilege` debe estar instalado en una ruta persistente y accesible
mediante `plugin_paths`. En una instalación no contenerizada puede instalarse con:

```bash
prosodyctl install --server=https://modules.prosody.im/rocks/ mod_privilege
```

En Docker, no instalarlo únicamente dentro de un contenedor desechable: montar o construir el
módulo de forma persistente.

### Privilegios

Agregar o completar el siguiente bloque en `prosody.cfg.lua`. Sustituir ambos dominios por los de
la instalación y conservar el `component_secret` que ya exista:

```lua
local slidge_privileges = {
    roster = "both";
    message = "outgoing";
    presence = "roster";
    iq = {
        ["jabber:iq:roster"] = "both";
        ["http://jabber.org/protocol/pubsub"] = "both";
        ["http://jabber.org/protocol/pubsub#owner"] = "set";
    };
}

modules_enabled = {
    -- Conservar aquí los demás módulos de la instalación.
    "privilege";
}

VirtualHost "xmpp.example.org"
    -- Conservar aquí authentication y las demás opciones del host.
    privileged_entities = {
        ["whatsapp.xmpp.example.org"] = slidge_privileges;
    }

Component "whatsapp.xmpp.example.org"
    component_secret = "CONSERVAR_EL_SECRETO_EXISTENTE"
    modules_enabled = { "privilege" }
```

Si se usa XEP-0363 para solicitar slots de subida en nombre del usuario, añadir también al bloque
`iq`:

```lua
["urn:xmpp:http:upload:0"] = "get";
```

Conceder estos permisos sólo al dominio exacto del puente. Es una entidad de confianza con
capacidad para modificar rosters y actuar en nombre de los usuarios del `VirtualHost`.

### Validación de Prosody

Validar la configuración antes de recrear servicios. Si Prosody ya está ejecutándose y monta el
archivo editado:

```bash
docker compose exec prosody prosodyctl check config
```

En la estructura usada por `marco-vps` también puede validarse sin alterar el servicio activo:

```bash
docker run --rm \
  -v /opt/xmpp/prosody/config:/etc/prosody:ro \
  -v /opt/xmpp/prosody/data:/var/lib/prosody:ro \
  -v /opt/xmpp/certs:/certs:ro \
  --entrypoint prosodyctl prosodyim/prosody:0.12 check config
```

Corregir cualquier error antes de continuar.

## Configuración para ejabberd

Declarar una ACL para el dominio exacto del puente y concederle los permisos equivalentes en
`ejabberd.yml`:

```yaml
acl:
  slidge_acl:
    server:
      - "whatsapp.xmpp.example.org"

access_rules:
  slidge_rule:
    - allow: slidge_acl

modules:
  mod_privilege:
    roster:
      both: slidge_rule
    message:
      outgoing: slidge_rule
    iq:
      "http://jabber.org/protocol/pubsub":
        both: slidge_rule
      "http://jabber.org/protocol/pubsub#owner":
        set: slidge_rule
  mod_roster:
    versioning: true
```

Si se usa HTTP Upload en nombre del usuario, añadir bajo `iq`:

```yaml
"urn:xmpp:http:upload:0":
  get: slidge_rule
```

Validar el archivo con las herramientas de la versión de ejabberd instalada antes de reiniciar.

## Configuración del puente

En el servicio `slidge-whatsapp` de `compose.yml`, seleccionar la imagen v10 y activar la
sincronización automática:

```yaml
services:
  slidge-whatsapp:
    image: ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v10
    environment:
      SLIDGE_WHATSAPP_ALWAYS_SYNC_ROSTER: "true"
```

Conservar sin cambios el comando, los volúmenes, el secreto, los dominios, la base de datos y las
demás variables existentes. Validar el Compose:

```bash
docker compose config -q
```

## Despliegue

Después de validar ambas configuraciones:

```bash
cd /opt/xmpp
docker compose pull prosody slidge-whatsapp
docker compose up -d --no-deps --force-recreate prosody
docker compose up -d --no-deps --force-recreate slidge-whatsapp
```

Si se usa ejabberd, sustituir `prosody` por el nombre real de ese servicio. El orden es
deliberado: el servidor XMPP debe anunciar los privilegios antes de que el puente conecte.

No debería solicitarse un QR nuevo porque se conservan los datos persistentes. Detenerse y
restaurar la configuración anterior si el puente pierde su sesión o solicita vincular WhatsApp
otra vez.

## Verificación

Revisar los servicios y los últimos registros:

```bash
docker compose ps
docker compose logs --since 10m --tail 250 prosody slidge-whatsapp
```

La instalación queda validada cuando:

1. Prosody o ejabberd inicia sin errores de configuración.
2. Slidge muestra `Successfully authenticated` y la cuenta recupera su sesión de WhatsApp.
3. Aparece `Automatic XMPP roster sync completed` después de conectar la cuenta.
4. No aparecen `Automatic XMPP roster sync failed` ni `IQ privileges not granted`.
5. Agregar o eliminar un contacto en WhatsApp se refleja en el roster tras la siguiente
   conexión, sin ejecutar scripts manuales.
6. Leer un mensaje desde WhatsApp oficial actualiza el cliente XMPP; en grupos se observa la
   publicación XEP-0490.

Una sincronización inicial puede tardar con cuentas grandes, pero un IQ de roster que termina en
`IqTimeout` no es un resultado correcto.

## Interpretación de errores

| Registro | Causa más probable | Acción |
| --- | --- | --- |
| `Automatic XMPP roster sync failed ... IqTimeout` | El IQ privilegiado de roster no recibió respuesta | Revisar `mod_privilege`, `privileged_entities`, dominio del componente y `roster = "both"` |
| `IQ privileges not granted` | Faltan permisos PubSub para XEP-0490 | Revisar los dos namespaces PubSub y reiniciar primero el servidor XMPP |
| El roster sincroniza pero los leídos de grupos no | Falta `pubsub#owner = "set"` o se usa Prosody antiguo | Completar IQ PubSub y usar Prosody 0.12 |
| No aparece ninguna sincronización automática | Falta la variable o no está activa la imagen v10 | Inspeccionar el Compose efectivo y la imagen del contenedor |
| El componente no autentica | Dominio, puerto o secreto no coinciden | Restaurar los valores propios del servidor; no copiar los de otra instalación |
| Se solicita QR después del despliegue | No se montaron los datos persistentes anteriores | Detenerse y restaurar el volumen/ruta de Slidge |

Los avisos de avatar o los handlers lentos de presencias `@lid` pueden tener otras causas. No se
debe asumir que desaparecen sólo por conceder estos privilegios; primero confirmar por separado
que el roster y XEP-0490 ya funcionan.

## Rollback

Si la validación falla:

1. Detener únicamente `slidge-whatsapp`.
2. Restaurar el archivo de configuración del servidor XMPP y `compose.yml` desde el respaldo.
3. Recrear primero el servidor XMPP y después el puente.
4. Conservar intactos los volúmenes y datos de Slidge.

No reescribir manualmente los archivos `roster/*.dat` de Prosody. La sincronización debe hacerse
mediante XEP-0356; modificar esos archivos directamente puede dejar el roster inconsistente.

## Referencias

- Privilegios de `slidge-whatsapp` y ejemplos para Prosody/ejabberd:
  https://slidge.im/docs/slidge-whatsapp/main/admin/privileges.html
- XEP-0356, Privileged Entity: https://xmpp.org/extensions/xep-0356.html
- XEP-0490, Message Displayed Synchronization: https://xmpp.org/extensions/xep-0490.html
- Implementación y prueba de la sincronización de leídos:
  `docs/PUENTE_WHATSAPP_SINCRONIZACION_LEIDOS.md`
- Contenido y construcción reproducible de la imagen v10: `docs/PUENTE_PERSONALIZADO.md`
