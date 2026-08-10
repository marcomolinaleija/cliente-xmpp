# Guía de despliegue del puente de WhatsApp

Esta es la guía principal para instalar y operar el puente XMPP/WhatsApp que
mantenemos Marco y el equipo de `cliente-xmpp`. Sirve tanto para un servidor
propio como para un NAS que tenga Docker Compose. Usa dominios, rutas y
secretos de ejemplo: no copies ninguno de otra instalación.

La imagen soportada actualmente es:

```text
ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v14
```

`v14` parte de `v13`, corrige el ciclo de vida de la renovación periódica de presencias y conserva
las mejoras que usa el cliente:
sincronización de roster y leídos, menciones nativas, reenvíos, stickers,
presencia, adjuntos de audio sin recodificación adicional y enrutamiento saliente mediante el
alias legado de contactos mexicanos duplicados. La descripción
técnica de esas extensiones está en [PUENTE_PERSONALIZADO.md](PUENTE_PERSONALIZADO.md).

## Qué instala esta guía

```text
Cliente XMPP ──> Prosody ──> componente whatsapp.xmpp.example.org ──> WhatsApp
                                  │
                                  └──> almacenamiento HTTP de adjuntos
```

Hay tres piezas persistentes:

| Pieza | Función | No borrar porque… |
| --- | --- | --- |
| Prosody | Cuentas XMPP y componente | guarda la identidad y configuración XMPP |
| Slidge | Sesiones vinculadas y base local del puente | contiene la vinculación de WhatsApp |
| Adjuntos | archivos recibidos desde WhatsApp | permite que el cliente descargue multimedia |

El usuario XMPP y el componente `whatsapp.xmpp.example.org` deben pertenecer
al mismo servidor XMPP. Esa condición es necesaria para que funcionen roster,
carbons, marcadores de lectura y acciones de la aplicación oficial.

## Antes de empezar

Necesitas:

- Un dominio para las cuentas XMPP, por ejemplo `xmpp.example.org`.
- Un subdominio distinto para el componente, por ejemplo
  `whatsapp.xmpp.example.org`.
- Un nombre HTTPS para los adjuntos, por ejemplo `files.example.org`.
- Docker Engine y el complemento Docker Compose.
- Un proxy inverso o la función equivalente del NAS para publicar XMPP y los
  adjuntos con TLS. No expongas el puerto interno del componente de Slidge a
  Internet.
- Espacio para la base de datos y los adjuntos, además de una copia de respaldo
  fuera del equipo.

El ejemplo usará `/srv/whatsapp-bridge`. En un NAS puede ser cualquier carpeta
compartida persistente. Cambia la ruta una sola vez y úsala de forma coherente.

## 1. Preparar las carpetas y los secretos

```bash
sudo mkdir -p /srv/whatsapp-bridge/{prosody,slidge,attachments,backups}
cd /srv/whatsapp-bridge
umask 077
openssl rand -hex 32
```

Guarda el resultado del último comando en un gestor de contraseñas. Créa un
archivo `.env` que no subirás a Git:

```dotenv
XMPP_DOMAIN=xmpp.example.org
COMPONENT_DOMAIN=whatsapp.xmpp.example.org
FILES_DOMAIN=files.example.org
COMPONENT_SECRET=PEGA_AQUI_UN_SECRETO_ALEATORIO
```

Protege ese archivo y exclúyelo de copias públicas:

```bash
chmod 600 .env
printf '.env\n' >> .gitignore
```

`COMPONENT_SECRET` no es una contraseña de usuario: es la clave compartida
entre Prosody y el puente. Debe ser larga, única y exactamente igual en ambas
piezas.

## 2. Configurar Prosody

Instala `mod_privilege` de manera persistente, no dentro de un contenedor que
se destruya al actualizar. En una instalación no contenerizada, el comando es:

```bash
prosodyctl install --server=https://modules.prosody.im/rocks/ mod_privilege
```

La imagen de Prosody usada con este puente debe ser `prosodyim/prosody:0.12`.
En la configuración de Prosody conserva los módulos y opciones que ya tenga el
servidor, y añade/adapta este bloque. Sustituye los tres valores entre ángulos;
no los dejes literales.

```lua
modules_enabled = {
    -- módulos existentes…
    "privilege";
    "http_files"; -- si se usa el modo de adjuntos de esta guía
}

local slidge_privileges = {
    roster = "both";
    message = "outgoing";
    iq = {
        ["http://jabber.org/protocol/pubsub"] = "both";
        ["http://jabber.org/protocol/pubsub#owner"] = "set";
    };
}

VirtualHost "xmpp.example.org"
    privileged_entities = {
        ["whatsapp.xmpp.example.org"] = slidge_privileges;
    }

Component "whatsapp.xmpp.example.org"
    component_secret = "EL_MISMO_SECRETO_DE_.env"
    modules_enabled = { "privilege" }

-- Debe coincidir con SLIDGE_NO_UPLOAD_PATH dentro del contenedor.
http_files_dir = "/var/lib/slidge-attachments"
```

El listener del componente debe estar disponible solamente en la red privada
de Docker. La configuración exacta cambia entre instalaciones de Prosody; si
ya existe, consérvala y conecta Slidge al nombre del servicio `prosody`.

Antes de reiniciar, valida el archivo:

```bash
docker compose exec prosody prosodyctl check config
```

Si el servidor XMPP existente es ejabberd, conserva ese servidor y aplica los
privilegios equivalentes de la documentación oficial. No instales Prosody en
paralelo solo para el puente.

## 3. Crear el Compose del puente

Guarda este archivo como `compose.yml`. Es intencionalmente una base: integra
sus servicios en la red, certificados y proxy que ya uses. No publica puertos
del componente ni contiene datos reales.

```yaml
services:
  prosody:
    image: prosodyim/prosody:0.12
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./prosody:/etc/prosody:ro
      - ./prosody-data:/var/lib/prosody

  slidge-whatsapp:
    image: ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v14
    restart: unless-stopped
    depends_on:
      - prosody
    env_file: .env
    environment:
      SLIDGE_JID: ${COMPONENT_DOMAIN}
      SLIDGE_SECRET: ${COMPONENT_SECRET}
      SLIDGE_SERVER: prosody
      SLIDGE_HOME_DIR: /var/lib/slidge
      SLIDGE_NO_UPLOAD_PATH: /var/lib/slidge-attachments
      SLIDGE_NO_UPLOAD_URL_PREFIX: https://${FILES_DOMAIN}/whatsapp/
      SLIDGE_NO_UPLOAD_FILE_READ_OTHERS: "true"
      SLIDGE_WHATSAPP_ALWAYS_SYNC_ROSTER: "true"
      SLIDGE_CONVERT_STICKERS: "true"
      SLIDGE_FIX_FILENAME_SUFFIX_MIME_TYPE: "true"
    volumes:
      - ./slidge:/var/lib/slidge
      - ./attachments:/var/lib/slidge-attachments

  attachments:
    image: nginx:alpine
    restart: unless-stopped
    volumes:
      - ./attachments:/usr/share/nginx/html/whatsapp:ro
      - ./nginx-attachments.conf:/etc/nginx/conf.d/default.conf:ro
```

El servicio `attachments` debe estar detrás de HTTPS. Un ejemplo mínimo de
`nginx-attachments.conf` para la red interna es:

```nginx
server {
    listen 80;
    server_name _;

    location /whatsapp/ {
        alias /usr/share/nginx/html/whatsapp/;
        autoindex off;
        try_files $uri =404;
    }
}
```

En el proxy inverso del servidor o NAS crea un host HTTPS para `FILES_DOMAIN`
que dirija la ruta `/whatsapp/` al servicio `attachments`. Comprueba desde una
red externa que la URL usa HTTPS y que un archivo de prueba se descarga. No
desactives TLS ni abras la carpeta de adjuntos como exploración de directorios.

### Alternativa: HTTP Upload de XMPP

Si tu servidor XMPP ya tiene XEP-0363, puedes usarlo en lugar del servicio
`attachments`: elimina las tres variables `SLIDGE_NO_UPLOAD_*`, configura el
servicio de subida y concede el permiso
`urn:xmpp:http:upload:0 = get` al componente. La documentación oficial detalla
ambos métodos. Para una instalación nueva, el método de adjuntos de esta guía
es más fácil de inspeccionar y respaldar.

## 4. Descargar y levantar la imagen

La imagen publicada es pública. Descárgala y valida el Compose antes de iniciar
por primera vez:

```bash
cd /srv/whatsapp-bridge
docker compose config -q
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --since 10m --tail 200 prosody slidge-whatsapp
```

Si el paquete pasa a ser privado, inicia sesión en GHCR con un token de lectura
antes de `docker compose pull`. No pongas ese token en `.env`, `compose.yml`,
historial de shell ni capturas.

El arranque es correcto cuando Prosody no muestra errores de configuración y
`slidge-whatsapp` se autentica como componente. Si se reinicia sin parar,
detente y revisa primero que los dos dominios y el secreto coincidan.

## 5. Crear cuentas y vincular WhatsApp

Hay dos registros distintos:

1. **Cuenta XMPP:** la crea el administrador del servidor.
2. **Cuenta de WhatsApp dentro del puente:** la realiza cada persona desde su
   propia cuenta XMPP y muestra un QR para vincular su WhatsApp.

Para Prosody, crea la cuenta XMPP desde el contenedor (cambia el ejemplo):

```bash
docker compose exec prosody prosodyctl adduser ana@xmpp.example.org
```

El comando pide la contraseña sin mostrarla. Guarda el JID y la contraseña en
el canal seguro que use tu organización; nunca dentro del Compose.

Después, la persona inicia sesión en `cliente-xmpp` con ese JID. En el panel de
vinculación el cliente registra la cuenta nueva ante el componente y muestra el
QR. Debe escanearse desde WhatsApp y esperar la confirmación antes de cerrar el
diálogo.

Con otro cliente XMPP, usa el comando ad-hoc **Register** contra el JID del
componente. Si el cliente no admite comandos ad-hoc, envía el texto `register`
al JID del componente y sigue sus indicaciones.

No reutilices una carpeta `slidge` para otra persona ni copies su base de
datos. Cada sesión de WhatsApp queda asociada a su cuenta XMPP.

## 6. Comprobación funcional

Haz esta prueba con una cuenta de prueba antes de incorporar usuarios reales:

1. Crear una cuenta XMPP y vincular WhatsApp con QR.
2. Enviar y recibir un texto.
3. Confirmar que aparecen los contactos y, si aplica, los grupos.
4. Enviar una imagen y una nota de voz; comprobar que la URL del adjunto abre
   por HTTPS y que el archivo sigue presente después de reiniciar el puente.
5. Leer un mensaje desde la aplicación oficial y confirmar que el estado llega
   al cliente XMPP.
6. Probar un sticker y un reenvío si esas funciones estarán disponibles para
   los usuarios.

Revisa también:

```bash
docker compose ps
docker compose logs --since 10m --tail 250 prosody slidge-whatsapp attachments
```

Se espera ver la autenticación del componente y la recuperación de la sesión
vinculada. Un QR inesperado después de una actualización indica que faltan o no
son escribibles los datos persistentes de `slidge`: no borres nada; restaura el
respaldo y revisa los montajes.

## Operación diaria

### Ver estado y registros

```bash
cd /srv/whatsapp-bridge
docker compose ps
docker compose logs --tail 200 slidge-whatsapp
```

### Reiniciar solamente el puente

Úsalo después de un cambio de configuración del puente. No reinicia Prosody ni
otros servicios que compartan el servidor:

```bash
docker compose up -d --no-deps --force-recreate slidge-whatsapp
```

### Detener y volver a levantar

```bash
docker compose stop slidge-whatsapp
docker compose start slidge-whatsapp
```

No ejecutes `docker compose down -v`. La opción `-v` elimina volúmenes y puede
perder la sesión de WhatsApp y los datos del puente.

## Respaldos y restauración

Antes de actualizar o cambiar configuración, respalda Compose, configuración de
Prosody y los datos persistentes. Para obtener una copia coherente de Slidge,
detén solamente el puente mientras se copia:

```bash
cd /srv/whatsapp-bridge
stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p "backups/$stamp"
cp -a compose.yml .env prosody nginx-attachments.conf "backups/$stamp/"
docker compose stop slidge-whatsapp
cp -a slidge attachments "backups/$stamp/"
docker compose start slidge-whatsapp
```

Protege el respaldo: contiene secretos y sesiones. Cífralo antes de moverlo
fuera del servidor y ensaya una restauración en un entorno aislado.

Para volver a un Compose conocido, restaura primero sus archivos, valida con
`docker compose config -q`, recrea Prosody si su configuración cambió y luego
recrea únicamente `slidge-whatsapp`. Conserva las carpetas `slidge` y
`attachments` salvo que estés restaurando una copia verificada de ellas.

## Actualizar nuestra imagen

Marco y el equipo mantienen la imagen y sus capacidades; por eso se actualiza
de forma controlada, no con una etiqueta desconocida ni reconstruyendo Slidge
por separado.

1. Revisar la etiqueta publicada y sus notas en el paquete de GitHub.
2. Respaldar como se indica arriba.
3. Cambiar únicamente `image:` a la etiqueta aprobada.
4. Validar, descargar y recrear solo el puente:

   ```bash
   docker compose config -q
   docker compose pull slidge-whatsapp
   docker compose up -d --no-deps --force-recreate slidge-whatsapp
   ```

5. Revisar registros y ejecutar la comprobación funcional de la sección 6.
6. Si falla, volver al tag anterior y al respaldo de configuración sin borrar
   las carpetas persistentes.

La publicación de una imagen nueva debe conservar los smoke tests del puente.
El detalle de los parches y pruebas está en
[PUENTE_PERSONALIZADO.md](PUENTE_PERSONALIZADO.md); no copies parches sueltos
en un servidor de producción.

## Problemas frecuentes

| Síntoma | Causa probable | Acción segura |
| --- | --- | --- |
| El puente no autentica | El JID del componente o el secreto difieren entre Prosody y Slidge | Comparar ambos valores sin imprimirlos; corregir y reiniciar solo el puente |
| El QR vuelve a aparecer tras actualizar | Falta el montaje de `slidge` o no tiene permiso de escritura | Detenerse, restaurar el montaje y recuperar el respaldo |
| No aparecen contactos o acciones hechas en WhatsApp | Faltan privilegios XEP-0356 o las cuentas no pertenecen al mismo servidor | Revisar `mod_privilege`, `privileged_entities` y los dominios |
| Llegan textos pero no archivos | La ruta/URL de adjuntos no coincide o el proxy no sirve la carpeta | Revisar `SLIDGE_NO_UPLOAD_*`, el montaje y el host HTTPS |
| Los adjuntos devuelven 404 después de enviarse | Se borró el archivo persistido o no se comparte la misma carpeta | Restaurar el montaje compartido; no limpiar adjuntos a ciegas |
| El contenedor se reinicia en bucle | Configuración inválida, secreto erróneo o dependencia no lista | Consultar los logs de Prosody y Slidge antes de cambiar nada |

## Reglas de mantenimiento

- Cada servidor tiene sus propios dominios, certificados, secretos y copias.
  Nunca copies esos valores desde otra instalación.
- Mantén `slidge`, `attachments` y la configuración fuera de la imagen y bajo
  respaldo.
- Restringe el registro a las cuentas XMPP de tu servidor; no conviertas el
  componente en un registro público.
- Da los privilegios XEP-0356 solo al dominio exacto del componente.
- Actualiza una pieza por vez y valida antes de pasar a la siguiente.
- No modifiques manualmente el roster interno de Prosody: el puente lo
  sincroniza mediante XMPP.

## Referencias

- [Paquete de la imagen mantenida](https://github.com/marcomolinaleija/cliente-xmpp/pkgs/container/cliente-xmpp-bridge)
- [Inicio rápido de slidge-whatsapp](https://slidge.im/docs/slidge-whatsapp/main/admin/quickstart.html)
- [Privilegios XEP-0356](https://slidge.im/docs/slidge-whatsapp/main/admin/privileges.html)
- [Adjuntos en slidge-whatsapp](https://slidge.im/docs/slidge-whatsapp/main/admin/attachments.html)
- [Configuración oficial de slidge-whatsapp](https://slidge.im/docs/slidge-whatsapp/main/admin/config.html)
- [Características y pruebas de nuestra imagen](PUENTE_PERSONALIZADO.md)
