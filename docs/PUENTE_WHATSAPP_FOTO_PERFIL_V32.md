# Protección de la foto propia de WhatsApp: v32

Slidge sincronizaba por defecto el avatar XMPP hacia WhatsApp. Al iniciar sesión,
una consulta XEP-0084 que respondía `item-not-found` invocaba `Session.on_avatar`
con datos vacíos. El binding traducía eso a una eliminación de la foto propia
en WhatsApp. Esta ruta existía en v30 y v31; se reprodujo de forma aislada,
con el envío sustituido por un doble y sin tocar fotos reales.

## Comportamiento de v32

- La foto propia se administra en WhatsApp. El handler de sincronización XMPP
  rechaza tanto datos vacíos como imágenes no vacías con `NotImplementedError`,
  que Slidge ya trata como una función no soportada. Así tampoco se sobrescribe
  la foto con un avatar XMPP antiguo si otro cliente reactiva la preferencia.
- El formulario del puente ofrece `sync_avatar=false` sin modificar los defaults
  de otras preferencias ni la clase base compartida.
- Antes de iniciar sesiones, el puente persiste `sync_avatar=false` en todas
  las cuentas existentes y limpia sólo su `avatar_hash` de sincronización.
  La migración es idempotente, usa el storage de Slidge y conserva las demás
  preferencias, las identidades y los datos de vinculación de WhatsApp.
- CAN envía `sync_avatar=false` al registrarse incluso con un puente antiguo
  que todavía anuncia `true`. Esto protege nuevos registros desde el código
  corregido; los clientes anteriores quedan protegidos al actualizar el puente.
- Las consultas de fotos de contactos y las ediciones explícitas de fotos
  de grupo conservan sus handlers. No se restauran fotos ya eliminadas.

## Imagen y comprobaciones

- Base fijada: `v31@sha256:46346cd8cd13e69bd9d4808027366fd92ebde23157b4ec1ebb90106d9a6d4cd5`.
- Imagen pública: `ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v32`.
- Digest: `sha256:c400f25c58c14ca9ce129b7b8f9091954da76154df445242319e59ddff4b1a2f`.
- ID de contenido: `sha256:1c56bdf1b6e6b572ab9353540ed8b383420efc521ee9f7d0aab3c1b9776b3968`.
- Receta: `tools/Dockerfile.bridge-avatar-safety-v32`. El parche valida los hashes
  de los dos módulos v31 antes de escribir y rechaza aplicaciones parciales
  o deriva de fuentes/helpers. Se aplica sólo durante el build, nunca en vivo.
- Se publicaron los mismos bytes probados; el acceso anónimo al registro y
  la descarga por digest confirmaron la identidad.
- Pasaron las siete pruebas de imagen: fotos, modos de audio, paquetes nativos,
  entrega/accesibilidad de stickers, stickers y ausencia de auto-join.
  El smoke de fotos también pasó sin red, con filesystem de imagen de sólo
  lectura y sin capacidades. Usa SQLite ficticio y setters WhatsApp simulados.
- El binding Go y los handlers de grupos y multimedia son idénticos a v31;
  este cambio sólo añade protección y migración Python, sin recompilar Go.
- Suite Python local completa: 803 métodos y 169 subcasos, sin fallos ni skips.
  Ruff pasó. La prueba del instalador calcula el hash de sus bytes CRLF públicos,
  exigidos por `.gitattributes`, incluso en un checkout LF. No cambia el instalador
  ni su checksum fijado, ni relaja la verificación de descargas.

## Actualización local y operación

El manifiesto estable conserva el contrato de etiqueta `vN` y digest obligatorio.
Los appliances modernos reciben v32 con la opción de actualizar puente en CAN
o con `manage-appliance.ps1 update`; no hace falta otro rootfs WSL.
La protección comienza al ejecutar v32, no sólo al actualizar el EXE del cliente.

La activación en servidor respalda el estado con el puente detenido y recrea
únicamente `slidge-whatsapp`. La reversión conserva los datos actuales y las
preferencias ya desactivadas; no restaura automáticamente una base antigua que
pudiera perder mensajes nuevos. El respaldo se mantiene privado en el servidor.
Las pruebas aisladas no equivalen a una prueba de cambio de foto real, que no
se ejecuta sobre las cuentas de los usuarios.

La activación de v32 del 10 de octubre de 2026 (hora de México) confirmó el arranque,
dos logins de WhatsApp y más de 65 segundos sin reinicios. Las cuatro cuentas
conservan sus identidades/datos de vinculación y las demás preferencias, con
`sync_avatar=false` y el hash de sincronización limpio. Prosody y el servicio
de adjuntos conservaron los mismos contenedores.

Respaldo y reversión privada: `/opt/xmpp/backups/bridge-v31-to-v32-20261011T003650Z/`.
`rollback.py` comprueba que el Compose no haya cambiado antes de restaurar v31;
el archivo `slidge-state.tar.sha256` permite verificar el respaldo consistente.
