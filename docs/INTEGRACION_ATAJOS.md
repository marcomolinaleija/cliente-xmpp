# Integración local con Atajos

En Configuración activa **Permitir integración local con Atajos**. La preferencia se conserva y la integración vuelve a iniciarse al abrir el cliente. Requiere la versión de Atajos que incluya las herramientas `xmpp_`. No introduce cambios en el servidor o puente de WhatsApp.

Atajos busca contactos y grupos del catálogo cargado. Cada solicitud admite uno a diez destinatarios y hasta 4000 caracteres por chat. Puede consultar el historial local para identificarlo o personalizar la petición. Gemini recibe nombres, identificadores opacos y el texto consultado como contexto. Los grupos conservan nombres de participantes y su identidad de sala. No se leen adjuntos, contraseñas ni comandos `/stats`, `/status` o `/transcribe`.

Por defecto Atajos ejecuta las peticiones de envío sin confirmación adicional. En **Mensajería…** puedes activar la revisión accesible con destinatarios, texto completo y hora, y configurar la cantidad inicial de contexto entre 1 y 400 (inicialmente 5). Una cantidad explícita prevalece sobre esa preferencia; todo el chat guardado se recorre por páginas. El cliente persiste la cola en `~/.cliente-xmpp/assistant-outbox.sqlite3`, separada de la base de conversaciones. No se cancela al cerrar Atajos. WhatsApp CAN debe estar abierto, con la integración activada y conectado para despacharla. Al volver a abrirlo o reconectar espera la cuenta original y envía los mensajes vencidos; nunca sustituye una cuenta o contacto. Un contacto retirado queda retenido.

Desactivar la integración conserva sus pendientes sin despacharlos. Una solicitud que ya esté en proceso puede haberse enviado. Los mensajes pendientes pueden consultarse y cancelarse desde Atajos. Si el resultado de un envío queda incierto no se repite automáticamente: revisa el chat. El protocolo del cliente conserva sus confirmaciones y mecanismos existentes de reintento con la misma identidad de mensaje.

CAN también permite [programar desde su menú](MENSAJES_PROGRAMADOS.md), sin activar Atajos. Ambas rutas comparten la base y el coordinador, pero conservan su origen: desactivar la integración no pausa mensajes creados directamente en CAN. La API consulta y cancela únicamente sus propios registros; el gestor de CAN muestra ambas rutas. Activar/desactivar la API no reabre ni recupera de nuevo la cola.

## Contrato versión 1

HTTP exclusivamente en `127.0.0.1:47843`; no se expone a la LAN ni al servidor. Todos los endpoints requieren `Authorization: Bearer ...`. No admite `Origin`, CORS, redirecciones ni hosts diferentes. La credencial de 43 caracteres la genera el cliente y guarda mediante el backend Windows de keyring, destino `WhatsAppCAN/Atajos`, usuario `Atajos`, blob UTF-16. Atajos la lee localmente; nunca se envía a Gemini ni se guarda en el JSON de configuración. La credencial autoriza una aplicación local del mismo usuario de Windows; no separa aplicaciones que ya puedan acceder a ese almacén.

| Endpoint | Función |
| --- | --- |
| GET `/v1/status` | Protocolo, conexión, identificador opaco de cuenta y política predeterminada. |
| GET `/v1/contacts?query=&offset=0` | Búsqueda sin tildes por nombre, 50 resultados por página. |
| POST `/v1/context` | Página de 1 a 400 mensajes guardados de un contacto individual, sin marcar leído. |
| POST `/v1/messages` | Acepta y persiste una solicitud idempotente. |
| GET `/v1/messages?state=all&offset=0` | Estado de la cola de la cuenta activa, 50 mensajes por página. |
| POST `/v1/messages/{id}/cancel` | Cuerpo `{}`; cancela solo `pending` o `held` de esa cuenta. |

El cuerpo de creación tiene exactamente `request_id` (UUID nuevo), `account_id` (de la búsqueda), `messages` (lista de `{contact_id,text}`), `send_at` (ISO 8601 con zona horaria) y `late_policy`. Atajos usa `send-when-connected`; `hold` es una alternativa del contrato que retiene envíos atrasados/desconectados. La API admite hasta un año de anticipación; el asistente limita a siete días. Rechaza campos extra, claves JSON duplicadas, contactos repetidos/desconocidos, texto inválido y solicitudes de otra cuenta. No acepta JID o teléfono como destino.

La consulta de contexto lleva exactamente `account_id`, `contact_id`, `count` (1..400) y `cursor` (vacío para los últimos, o `next_cursor` de la página anterior). El cursor es opaco, dura diez minutos y pertenece exclusivamente a esa cuenta y contacto. La paginación ordena por fecha e identidad local, conserva mensajes con la misma fecha y excluye inserciones posteriores al inicio de la lectura. Atajos conserva el cursor sin enviarlo al modelo; su herramienta usa `latest` o `older`.

La respuesta indica `source=local-cache`, `local_total`, `returned_count`, `requested_count`, `has_more`, `page_limited`, `next_cursor` y `marks_read=false`. Cada mensaje tiene texto, fecha, dirección (`outgoing`), tipo genérico, retracción y `text_truncated`. La página tiene un presupuesto de tamaño para caber en el contexto del asistente; un mensaje muy largo se recorta con una indicación explícita. Los mensajes eliminados localmente se excluyen y los retraídos nunca exponen su texto anterior. No incluye JID, URL de adjunto o ruta de archivo como metadatos; el cuerpo escrito por el usuario se conserva como texto.

El lector abre SQLite en modo de solo lectura desde un worker. No abre el chat, modifica borradores o no leídos, envía recibos ni consulta MAM. «Todo» significa todo lo guardado en el cliente, no todo el servidor. Para leer más historial primero debe estar cargado en el cliente. En chats grandes puede ser necesario pedir continuar varias veces, o pulsar **Nueva conversación** y continuar hacia los mensajes anteriores antes de que caduque el cursor. Atajos informa límites en lugar de asegurar una lectura completa.

Respuesta 202 significa **guardado**, no entregado. Repetir el mismo `request_id` y contenido devuelve los mismos IDs; reutilizarlo con otro contenido falla. El cliente limita a 500 entradas pendientes/en proceso/retenidas. No repitas peticiones con un UUID nuevo después de un fallo incierto: consulta primero la cola.

Estados: `pending`, `held`, `dispatching`, `submitted`, `delivered`, `read`, `failed`, `uncertain`, `canceled`. La transición a `dispatching` se guarda antes del envío; al reiniciar, un envío sin confirmación se convierte en `uncertain` y no se repite. El ID XMPP es `cliente-xmpp-api-{id}` y usa la ruta normal de envío optimista y eventos de entrega. Confirmaciones tardías pueden resolver `uncertain`; `read` no retrocede a `delivered` ni a `failed`.

La API y SQLite trabajan en un hilo independiente. El catálogo se copia desde wx y el envío regresa al hilo wx mediante `CallAfter`; el servicio comprueba otra vez la cuenta y la conexión en su hilo XMPP. Cambiar compositor, borrador, foco o chat abierto no es necesario para enviar. Sin abrir el cliente no existe un proceso que despache la cola.

## Prueba manual

Usa un contacto de pruebas autorizado: programa un mensaje a un minuto y comprueba que la preferencia desactivada evita la revisión. Actívala desde **Mensajería…** y verifica Confirmar/Cancelar con teclado y NVDA. Programa otro, cierra Atajos y comprueba el envío. Desconecta/cierra WhatsApp CAN y verifica que espera la reconexión original. Consulta/cancela un pendiente y comprueba que no sale. Pide leer cinco mensajes, luego 400 y después todo el chat guardado, verificando cantidades, dirección, páginas y límites. Comprueba que no cambia el chat abierto, borrador, foco ni los no leídos.

Las pruebas automáticas de `tests/test_atajos_api.py` usan bases temporales, contactos ficticios y un callback de envío simulado; no mandan mensajes a personas.

## Adjuntos para el asistente

WhatsApp CAN 1.4.15 anuncia `media=true` en `/v1/status`. Atajos 1.1.6 añade análisis de imágenes, audio y vídeo con Gemini. El asistente interactivo consulta los adjuntos y obtiene únicamente el elegido por el usuario; las reglas autónomas no reciben estas herramientas.

- `POST /v1/media`: exactamente `account_id`, `contact_id` (ID de contactos/grupos o vacío para todos los chats disponibles), `kind` (`image`, `audio`, `video`, `all`) y `count` (1..10). Devuelve los últimos recibidos por fecha e identidad local. Incluye nombre del chat, tipo, fecha, nombre del archivo, tamaño y un ID temporal; no incluye cuerpos, rutas, JID o URLs.
- `POST /v1/media/content`: exactamente `account_id` y `media_id`. Devuelve los bytes de un adjunto seleccionado anteriormente, con `X-Atajos-Account` y `X-Atajos-Media-Mime`; la credencial Bearer y las restricciones de origen/host son las mismas que en los otros endpoints. El contenido no se guarda en la caché HTTP.

La selección caduca a los diez minutos, pertenece a una cuenta/chat/mensaje y se revalida antes/después de leer. No ofrece medios salientes, retraídos, eliminados localmente, de otra cuenta o chats fuera del catálogo. Las copias locales deben estar dentro de downloads/clipboard administrados y no contener enlaces. Si falta la copia, el cliente descarga en un worker a `.part`, con límite de 100 MiB tanto por cabecera como durante el streaming, y persiste la ruta solo tras el éxito. Una retracción durante la descarga impide devolver el contenido y retira solo la nueva copia. No abre chats, envía recibos, modifica borradores o manda respuestas. La consulta funciona sobre la caché local; no consulta MAM ni garantiza todo el historial remoto.

Los archivos elegidos se envían a Google desde Atajos con la clave Gemini del usuario. El cliente solo los entrega por la API local autenticada; no tiene una nueva clave Gemini ni ejecuta instrucciones procedentes de archivos. Atajos tiene progreso, cancelación y un resultado completo para lectura/copia.

Para probar: reinicia el cliente actualizado con la integración activa, pide describir una foto ficticia recibida, luego el último audio de un contacto de pruebas y un vídeo de un grupo. Comprueba que no cambia el foco, no reproduce sonidos, no marca leído y no envía mensajes. Repite sin copia local, con cuenta cambiada, adjunto retraído y límite de tamaño. Las pruebas automáticas `test_atajos_media.py` usan bases temporales, archivos y contactos ficticios, sin conexión real.

## Respuestas autónomas

Atajos 1.1.4 añade reglas por chats seleccionados, contactos, grupos o toda la cuenta, con exclusiones. Se activan expresamente, con intervalo, duración, contexto de 1 a 400 mensajes e instrucciones. El motor sigue en Atajos y Gemini redacta sin herramientas. La confirmación de Atajos produce borradores. Reiniciar requiere reactivar y no responde al historial acumulado; la cola manual conserva su política de reconexión.

El cliente registra texto en vivo con identidad estable; MAM, inbox e historial no inician respuestas. Mensajes propios, ediciones, eliminaciones, medios, encuestas y llamadas invalidan borradores anteriores. El diario persistente está en `assistant-outbox.sqlite3`, separado de la conversación. Vaciar un chat elimina sus textos del diario; editar/eliminar invalida el desencadenante original. Los grupos autorizados se supervisan por lotes de diez cada tres segundos, sin abrirlos, marcar lectura ni cargar todo el historial.

API nueva, con la misma autenticación local y rechazo de orígenes web:

- `GET /v1/contacts?kind=contacts|groups|all`: catálogo paginado con `is_group`; por defecto conserva individuos.
- `POST /v1/automation/lease`: permiso vinculado a cuenta y sesión del diario; alcance y exclusiones inmutables hasta revocar. Atajos concede tres minutos y renueva mientras esté activo; la API limita permisos a cinco minutos. Activar devuelve el final del diario para omitir el pasado.
- `POST /v1/automation/events`: novedades desde `after_seq`, con identidad opaca, tipo, participante y secuencia. La paginación avanza también sobre chats retirados.
- `POST /v1/automation/reply`: UUID persistente, regla, chat y secuencia desencadenante. Comprueba que el último evento siga siendo entrante, que el permiso esté vigente y que no exista otra respuesta para ese desencadenante.
- `POST /v1/automation/revoke` y `/revoke-all`: revocan permisos y cancelan pendientes automáticos. Primero invalidan en memoria los callbacks ya encolados.
- `GET /v1/requests/{id}?account_id=…`: reconcilia sin reenviar.

Los handlers wx revalidan sin SQLite y el protocolo comprueba el permiso justo antes del envío. Las respuestas automáticas no usan reintentos transitorios; los mensajes normales conservan los suyos. Un envío ya entregado al protocolo puede estar en curso. Reiniciar cambia la sesión del diario y retiene pendientes automáticos; cuentas diferentes, permisos caducados o contexto modificado exigen revisar/reactivar.

Las pruebas `test_atajos_automation.py` y `test_conversation_context.py` usan datos ficticios, bases temporales y envío simulado. Para probar manualmente, empieza con un contacto de prueba en modo borrador; después prueba un grupo. Comprueba una sola respuesta ante ráfagas, cancelación al responder manualmente durante la generación, pausa, reinicio y cambio de cuenta. Verifica el modo automático consultando el estado de su solicitud, sin repetir un resultado incierto.

## Certificados del servidor remoto

La integración local no requiere modificar certificados del servidor. Si el cliente remoto no conecta por TLS, revisa la cadena, los nombres y la vigencia en STARTTLS 5222 y los puertos HTTPS configurados.

Las plantillas [repair-certificates.sh](../tools/xmpp/repair-certificates.sh) y [deploy-certificates.sh](../tools/xmpp/deploy-certificates.sh) usan dominios ficticios. Antes de ejecutarlas, adapta ambos archivos a tu dominio, rutas de Certbot, Nginx, contenedor y certificados montados. Requieren sudo y están diseñadas para Prosody en Docker; no son un instalador universal.

La reparación guarda respaldos privados, instala un deploy hook propiedad de root, solicita la renovación y sincroniza certificados ya renovados con sus copias de servicio. Verifica confianza, nombre y huella en los puertos configurados. Recarga Nginx y Prosody; si el contexto TLS no cambia, reinicia exclusivamente el contenedor indicado. Este reinicio interrumpe brevemente las conexiones. El hook también requiere que prosodyctl pueda localizar el proceso mediante pidfile y el módulo posix.

Referencias oficiales: [renovaciones y deploy hooks de Certbot](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates) y [pidfile de prosodyctl](https://prosody.im/doc/prosodyctl#pidfile).
