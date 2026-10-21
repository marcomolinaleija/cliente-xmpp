# Actualizar desde una versión anterior: llamadas de voz

La appliance nueva incluye el motor de llamadas y configura voz **siempre
habilitada** (`CAN_VOICE_ENABLE=1`). La instalación, la migración y el arranque
preparan su funcionamiento sin un paso manual de activación. El servicio de
voz disponible no abre el micrófono: la captura sólo comienza al llamar o
contestar desde el cliente.

## Compatibilidad durante la actualización

Actualizar el cliente Windows permite seguir usando mensajes, historial y
adjuntos con un puente anterior. Si ese puente todavía no ofrece voz, el
cliente informa que el canal no está disponible sin cerrar por ello XMPP.
El historial de llamadas tampoco equivale a poder hablar desde Windows.

Para obtener llamadas hay que actualizar el puente. La nueva appliance sólo
se da por instalada cuando el motor y el servicio WSS pasan sus comprobaciones.
No termina silenciosamente con voz desactivada. Si falla una migración, recupera
la appliance anterior y conserva también el estado de la candidata para poder
revisar datos que hayan llegado durante el intento.

## Appliance local WSL2

`whatsapp-can-bridge update` actualiza la imagen del contenedor.
`actualizar-puente-local.ps1` actualiza la appliance completa. Las versiones
anteriores necesitan la actualización completa para incorporar certificados,
montajes y configuración de llamadas.

La migración desde 1.0/1.1 conserva credenciales, CA, sesión, bases, adjuntos,
Prosody y opciones adicionales del entorno. Respalda la imagen activa y adopta
la incluida sólo si es más reciente; conserva una instalada de igual versión
o posterior. Si esa imagen conservada no incluye un motor compatible, rechaza
la preparación y recupera la versión anterior en lugar de entregar voz apagada.

La appliance prepara automáticamente:

- Un certificado para `127.0.0.1`, firmado por la CA de la instalación.
- El endpoint `wss://127.0.0.1:8766/voice/v1`, con certificado y clave legibles
  por Slidge. La clave de la CA no se monta en el contenedor.
- El entorno de voz y el arranque del motor; las opciones antiguas de voz
  desactivada no prevalecen sobre esta configuración.
- Comprobaciones de servicios, TLS y rechazo de solicitudes anónimas.

No necesita dominio público, proxy adicional ni publicar el puerto en Internet.
La CA del perfil local permite al cliente verificar WSS sin cambiar la confianza
global de Windows. El perfil y las credenciales remotas siguen siendo independientes.

Consulta el estado desde PowerShell:

```powershell
wsl.exe -d WhatsAppCAN-Bridge -u root -- /usr/local/sbin/whatsapp-can-bridge voice status
wsl.exe -d WhatsAppCAN-Bridge -u root -- /usr/local/sbin/whatsapp-can-bridge smoke
```

`voice enable` queda como comando de reparación: vuelve a comprobar la imagen,
certificados y servicio. No es un paso normal de instalación. `voice disable`
se rechaza porque voz es una función obligatoria de la appliance. Un fallo de
su comprobación se comunica como error y no cambia automáticamente el entorno
a `CAN_VOICE_ENABLE=0`.

Los actualizadores antiguos descargados desde la release 1.1 siguen fijados a
esa entrega. Hay que obtener el actualizador nuevo cuando se publique. Mientras
el manifiesto estable siga en 1.1, su ejecución no instala una candidata 1.2.

## Servidor propio, VPS o NAS

El administrador debe preparar una entrega que incluya motor Go/WaCalls,
binding nativo, proxy Python y entrypoint de voz. Añadir variables a una imagen
anterior sin esos componentes no incorpora llamadas.

| Variable | Configuración de la entrega con llamadas |
| --- | --- |
| `CAN_VOICE_ENABLE` | `1` en funcionamiento normal |
| `CAN_VOICE_URL` | URL WSS accesible desde Windows, terminada en `/voice/v1` |
| `CAN_VOICE_CERT`, `CAN_VOICE_KEY` | Certificado y clave montados, legibles por el usuario del puente |
| `CAN_VOICE_BIND`, `CAN_VOICE_PORT` | Listener interno; por defecto `127.0.0.1` y `8766` |
| `CAN_VOICE_HOME` | HOME efectivo de Slidge; por defecto `/var/lib/slidge` |
| `CAN_VOICE_SOCKET` | Lo genera el entrypoint; no se copia una ruta histórica ni se crea el socket a mano |

El certificado debe ser válido para el host anunciado. Un proxy inverso debe
permitir WebSocket y verificar el TLS del servicio interno. Protege la clave
privada y no montes la clave de la CA. El HOME debe existir y permitir crear el
directorio privado del socket antes de iniciar Slidge; un error en esa fase
puede impedir el arranque del contenedor, incluida la mensajería.

El cliente obtiene URL y autorización por XMPP. No se introduce un token de voz
a mano ni se desactiva TLS. Las métricas y el perfilador son opcionales.

## Comprobación después de actualizar

1. Respalda la instalación y conserva separados los perfiles local y remoto.
2. Actualiza el cliente y después la appliance completa o el despliegue remoto
   preparado con llamadas. No desvincules WhatsApp ni repitas el QR como paso
   rutinario de actualización.
3. Comprueba conexión, envío y recepción, historial y adjuntos. En la appliance,
   verifica que voz funciona inmediatamente y después de reiniciar, sin ejecutar
   un comando de activación.
4. Con un contacto de prueba autorizado, comprueba llamadas entrantes y salientes,
   audio en ambos sentidos, silencio y fin; repite mensajes después de colgar.
   TLS y un arranque correcto no acreditan audio audible.
5. Si la actualización falla, conserva los respaldos y el estado de la candidata.
   No restaures una base antigua sobre mensajes nuevos ni borres volúmenes.

La voz sigue siendo experimental: individual, sin vídeo ni llamadas grupales.
Las pruebas automatizadas con fixtures no garantizan calidad en todas las redes.

## Estado y referencias

La candidata anterior, con voz desactivada por defecto, quedó sustituida por el
requisito de voz obligatoria. Su auditoría no acredita el nuevo paquete. La
[auditoría de la candidata](VALIDACION_APPLIANCE_1_2.md) identifica los artefactos
y resultados disponibles. Las candidatas locales no anuncian una publicación
en GitHub o GHCR.

- [Preparación y actualización de la appliance WSL2](PUENTE_WHATSAPP_WSL2.md).
- [Despliegue en servidor o NAS](GUIA_DESPLIEGUE_PUENTE_WHATSAPP.md).
- [Evolución experimental de voz](PUENTE_WHATSAPP_VOZ_EXPERIMENTAL_V35.md).
