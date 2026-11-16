# Corrección de la versión Web del puente remoto

El puente remoto rechazaba la conexión con `Client outdated (405)` porque
anunciaba `2.3000.1042386815`. La corrección candidata actualiza el baseline
nativo a `2.3000.1052556849`, conservando el fork de llamadas, la imagen R7 y sus
parches. No cambia credenciales ni inicia otra vinculación.

## Alcance y motivo

- La versión rechazada apareció en los logs reales después del reinicio.
- El número estaba fijado en `store/clientpayload.go`; reiniciar no lo cambia.
- El binario usa el reemplazo local `/opt/bridge-whatsmeow`, no el vendor que
  acompaña a Python. El parche valida y actualiza **ambas fuentes** y reconstruye
  la extensión nativa. Cambiar sólo el `.go` del runtime no corrige el binario.
- Se añade un log de arranque con el número efectivamente compilado, sin
  identidades, mensajes, claves ni códigos de vinculación.
- Se retira del contrato de llamadas la exigencia del antiguo número Web. La
  capacidad `SupportCallLogHistory` sigue siendo obligatoria y se prueba junto
  al payload de autenticación y la versión compilada.

El baseline proviene de [Whatsmeow, revisión
54038813c47f3f351eb6fcaafab0e1b99d676850](https://github.com/tulir/whatsmeow/blob/54038813c47f3f351eb6fcaafab0e1b99d676850/store/clientpayload.go#L72-L91).
El mismo código advierte que modificar el número no sustituye una actualización
de protocolo cuando ésta es necesaria. Por eso, el candidato sólo se acepta
tras autenticación real, no únicamente por compilar o tener health positivo.
No se introduce una actualización automática ni una dependencia HTTP al
arrancar: la consulta pública a WhatsApp desde el VPS respondió HTTP 400.

## Construcción aislada

`tools/Dockerfile.bridge-web-version` se superpone al R7 auditado y emplea el
builder nativo existente. Antes de construir hay que verificar los ImageID
locales; las etiquetas por sí solas no son inmutables:

| Imagen | ImageID esperado para este corte |
|---|---|
| R7 `archive-delivery-r7-staging-20261025` | `sha256:d50d94cd842e49f4facb1bc15058f5cbb3e99049da983d5713508c3a9522b922` |
| `linking-builder-20261020` | `sha256:6d02f05a45ae0c46a2107b8fc78c630085cf80c7304c61841393977c4f18960e` |

El build ejecuta sin red pruebas Go normales y con `-race`, compila con las
etiquetas de multimedia existentes y corre smokes aislados de vinculación,
eco, lectura, recuperación, archivo y contrato de llamadas. No monta bases
productivas ni publica imágenes. El overlay está acotado a esta base; no debe
aplicarse a otra imagen sin volver a auditarla.

El parche anterior de llamadas v27 y sus Dockerfiles son históricos. No se
deben reaplicar después de este overlay para restablecer el baseline viejo.
El repositorio hermano `puente-whatsapp` conserva el mismo baseline en su
override y una regresión Go para sus futuras construcciones.

## Barreras de despliegue

El corte autorizado debe:

1. Respaldar configuración y bases con la API SQLite online en un directorio
   privado; comprobar los respaldos sin imprimir datos personales.
2. Sustituir sólo `slidge-whatsapp`, conservando montajes, entorno, red, límites,
   política de reinicio y el watchdog existente.
3. Verificar imagen exacta, salud funcional XMPP repetida, log de versión nueva
   y login exitoso de la cuenta afectada. Health por sí solo no es suficiente.
4. Comprobar identidad vinculada conservada y ausencia de rechazo 405, fallos
   de archivo, bloqueos SQL, pánicos o reinicios inesperados.
5. Revertir imagen/configuración si falla la aceptación. **No restaurar una base
   antigua**: eso podría eliminar mensajes recibidos durante el corte.

## Resultado del corte autorizado

La imagen local `cliente-xmpp-bridge:web-version-staging-20261102` quedó activa
el **2 de noviembre de 2026 a las 17:50:26, Ciudad de México** (23:50:26 UTC).
ImageID: `sha256:a5c1e8f6a557dd04054f0653831868252655886e3edaa6736a00f3a35abed919`.
SHA-256 nativo: `6280aa56d438787e2b636dc4ab30224a2a9b4e5552463618d01dd6959e216682`.

- Login de la cuenta afectada confirmado a las 23:50:32 UTC, sin revinculación;
  identidad del dispositivo y claves de identidad/transporte conservadas.
- Salud funcional repetida aprobada, health `healthy`, sin reinicios durante
  aceptación; configuración protegida, watchdog y otros 27 contenedores iguales.
- Cero rechazos `client_outdated` y cero fallos de archivo, bloqueos SQL,
  pánicos o `ParseError` durante aceptación.
- Go y `-race`: 87 casos incluidos subtests en siete paquetes; la ampliación de
  regresiones de llamadas pasó 29 casos del paquete principal, también con
  `-race` (incluye casos ya presentes, no son 116 casos únicos).
- Seis smokes aislados y seis pruebas del parche del puente con once subcasos
  aprobados, además de lint focalizado y `git diff --check` en ambos repositorios.

El primer build se detuvo por un contrato v27 que aún buscaba el historial con
`s.ctx` y mensajes exclusivamente en `session.go`. Se reprodujo el mismo fallo
en R7 sin modificar. El contrato actualizado acepta el helper con `ctx`, consulta
el dueño actual `can_inbound.go` y conserva las comprobaciones de llamadas/XML;
pasó tanto en R7 como en la candidata. No se omitió la validación fallida.

Respaldo privado y reversión:
`/opt/xmpp/backup-web-version-20261102T235021Z/rollback.sh`.
La reversión cambia imagen/configuración, no restaura SQLite antiguo.
Evidencia de build, pruebas y corte:
`/opt/xmpp/web-version-20261102-xROz1r/` (acceso privado de root).
Son referencias de **este corte**, no instrucciones para ejecutarlo otra vez.

No hubo GHCR, commit, push ni tag. Por indicación del usuario no se repitieron
pruebas del cliente CAN después de limitar el alcance al puente. Queda pendiente
su prueba real de envío/recepción; las llamadas reales y la estabilidad prolongada
no quedan acreditadas por el login ni por los smokes.

## Publicación posterior desde el repositorio independiente

Después de la prueba humana de envío/recepción y llamadas, se integraron las
fuentes del VPS y la rama de voz en `marcomolinaleija/puente-whatsapp`, preservando
la protección contra unión automática de ese repo. Se reconstruyó y publicó
`ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v36` desde el commit
`4f57b58fece4754ae013aeb1f97a3d344c616077`, no mediante un retag de la staging.

Digest publicado y comprobado mediante descarga anónima:
`sha256:5a8ed6cb7a531878e6b29afc58047b08beab096a42dd896651a19139247d295e`.
El manifiesto estable de este cliente se actualiza con esa identidad. El código
fuente y la publicación futuros del puente se mantienen en el repo independiente;
las herramientas de este documento conservan la cronología del arreglo previo.

La imagen integrada pasó Go/race (216 casos en cada ejecución), 23 comprobaciones
de runtime y el probe nativo. Los vectores públicos de audio se descargan sólo
en el stage de tests y no se publican dentro de la imagen. No se construyó una
nueva versión Windows ni se cambió el contenedor productivo por esta publicación.
