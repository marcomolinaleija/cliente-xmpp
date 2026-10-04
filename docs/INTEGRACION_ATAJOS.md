# Integración local con Atajos

En Configuración activa **Permitir integración local con Atajos**. La preferencia se conserva y la integración vuelve a iniciarse al abrir el cliente. Requiere la versión de Atajos que incluya las herramientas `xmpp_`. No introduce cambios en el servidor o puente de WhatsApp.

Atajos busca contactos individuales del catálogo cargado en el cliente. Cada solicitud admite uno a diez destinatarios y hasta 4000 caracteres de texto por persona. También puede consultar el historial local del contacto seleccionado para identificarlo o personalizar lo solicitado. Las búsquedas comparten nombres e identificadores opacos con Gemini; el texto consultado del chat también se envía a Gemini como contexto. No se consultan grupos, archivos adjuntos, contraseñas ni comandos `/stats`, `/status` o `/transcribe`.

Por defecto Atajos ejecuta las peticiones de envío sin confirmación adicional. En **Mensajería…** puedes activar la revisión accesible con destinatarios, texto completo y hora, y configurar la cantidad inicial de contexto entre 1 y 400 (inicialmente 5). Una cantidad explícita prevalece sobre esa preferencia; todo el chat guardado se recorre por páginas. El cliente persiste la cola en `~/.cliente-xmpp/assistant-outbox.sqlite3`, separada de la base de conversaciones. No se cancela al cerrar Atajos. WhatsApp CAN debe estar abierto, con la integración activada y conectado para despacharla. Al volver a abrirlo o reconectar espera la cuenta original y envía los mensajes vencidos; nunca sustituye una cuenta o contacto. Un contacto retirado queda retenido.

Desactivar la integración conserva los pendientes sin despacharlos. Una solicitud que ya esté en proceso puede haberse enviado. Los mensajes pendientes pueden consultarse y cancelarse desde Atajos. Si el resultado de un envío queda incierto no se repite automáticamente: revisa el chat. El protocolo del cliente conserva sus confirmaciones y mecanismos existentes de reintento con la misma identidad de mensaje.

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

## Certificados del servidor remoto

La integración local no requiere modificar certificados del servidor. Si el cliente remoto no conecta por TLS, revisa la cadena, los nombres y la vigencia en STARTTLS 5222 y los puertos HTTPS configurados.

Las plantillas [repair-certificates.sh](../tools/xmpp/repair-certificates.sh) y [deploy-certificates.sh](../tools/xmpp/deploy-certificates.sh) usan dominios ficticios. Antes de ejecutarlas, adapta ambos archivos a tu dominio, rutas de Certbot, Nginx, contenedor y certificados montados. Requieren sudo y están diseñadas para Prosody en Docker; no son un instalador universal.

La reparación guarda respaldos privados, instala un deploy hook propiedad de root, solicita la renovación y sincroniza certificados ya renovados con sus copias de servicio. Verifica confianza, nombre y huella en los puertos configurados. Recarga Nginx y Prosody; si el contexto TLS no cambia, reinicia exclusivamente el contenedor indicado. Este reinicio interrumpe brevemente las conexiones. El hook también requiere que prosodyctl pueda localizar el proceso mediante pidfile y el módulo posix.

Referencias oficiales: [renovaciones y deploy hooks de Certbot](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates) y [pidfile de prosodyctl](https://prosody.im/doc/prosodyctl#pidfile).
