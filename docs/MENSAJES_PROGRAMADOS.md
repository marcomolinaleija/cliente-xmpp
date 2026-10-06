# Programar mensajes desde CAN

**Mensajes > Programar mensaje...** guarda un texto para un contacto y una fecha,
sin necesitar Atajos. CAN debe permanecer abierto, el equipo despierto y la cuenta
original conectada para despacharlo. No se programa el envío en el servidor.

## Programar

1. Carga los contactos de la cuenta. Después puedes programar aunque estés desconectado.
2. Abre **Mensajes > Programar mensaje...**. El foco inicial está en la lista de
   contactos; el chat actual queda preseleccionado si es un contacto disponible.
3. Elige el contacto con las flechas. Puedes filtrar con **Buscar contacto**;
   la búsqueda ignora mayúsculas y tildes. Los nombres repetidos muestran un
   identificador adicional para no confundir destinatarios.
4. Escribe el texto, de 1 a 10 000 caracteres. Este formulario no modifica el borrador
   del chat. No admite comandos locales del puente.
5. La fecha ya está rellenada en **DD/MM/AA**, por ejemplo **06/10/26**, y la hora
   usa 24 horas, por ejemplo **18:30**. Inicialmente se propone dentro de quince
   minutos: normalmente hoy, o mañana si se cruza la medianoche. Puedes cambiarlas;
   deben indicar un instante futuro, hasta un año de anticipación. El año de dos
   dígitos corresponde a 2000–2099: **26** significa **2026**.
6. Elige qué hacer si el envío se retrasa. Revisa el resumen completo de contacto,
   cuenta, fecha, zona, política y texto; confirma para guardarlo.

Los cambios de horario que hagan una hora inexistente o ambigua se rechazan con
una explicación. Se usan las reglas de Windows para la fecha elegida, no el
desfase de hoy. Se guarda un instante fijo: cambiar después la zona horaria no
desplaza el envío. Un cambio del reloj del equipo sí afecta cuándo vence.

| Política | Comportamiento |
| --- | --- |
| **Retener si no se puede enviar a tiempo** (predeterminada) | Si está desconectado al vencer o llega con más de 30 segundos de atraso, queda retenido para revisión; no se manda horas después por sorpresa. |
| **Enviar al volver a conectar** | Espera sin límite de atraso a la cuenta y conexión originales. Puede enviarse al abrir CAN mucho después de la fecha. |

Cerrar el diálogo antes de confirmar no programa nada. Durante el guardado no se
puede cancelar/cerrar el formulario: espera su resultado. Si falla, conserva los
datos y la misma identidad de solicitud para reintentar sin duplicar. Ante un
resultado incierto, consulta la lista antes de crear otra programación.

## Consultar y cancelar

**Mensajes > Mensajes programados...** muestra los mensajes de la cuenta seleccionada,
incluidos los de Atajos, con su origen. El filtro inicial reúne pendientes y
problemas; **Todos** incluye el historial de estados. Hay 50 registros por página.

- Flechas eligen un registro. **Enter** enfoca los detalles y el texto completo,
  disponibles para lectura y copia. La lista no contiene cuerpos ni tooltips largos.
- **Actualizar** o **F5** consulta los estados. No se reconstruye automáticamente
  mientras lees. Al actualizar conserva la selección por identidad.
- **Cancelar mensaje...** pide confirmación y sólo cancela pendientes/retenidos.
  Si el envío comenzó durante la confirmación, informa que no se pudo cancelar.
- Un mensaje retenido no se reactiva solo: revisa, cancélalo y programa uno nuevo.
  No hay edición directa, recurrencias, adjuntos ni programación nativa a grupos.

| Estado | Qué significa |
| --- | --- |
| Pendiente | Guardado; espera la hora, cuenta o conexión. |
| Retenido | No se envió; necesita revisión. |
| Envío en curso | El despacho comenzó; ya no se puede cancelar. |
| Enviado al servicio | No confirma entrega al destinatario. |
| Entregado / Leído | El protocolo informó esa confirmación; depende del puente/chat. |
| Fallido | No se repite automáticamente desde la cola. |
| Resultado incierto | Comprueba el chat antes de repetir; pudo haberse enviado. |
| Cancelado | Se retiró antes de iniciar el envío. |

## Teclado

El menú **Mensajes** usa **Alt+E**, sin reemplazar **Alt+M** de marcar chats leídos.
Dentro del formulario: **Alt+B** busca, **Alt+C** enfoca contactos, **Alt+M** el
texto, **Alt+F** la fecha, **Alt+H** la hora, **Alt+R** la política y **Alt+P** el
botón de revisión (Espacio lo activa). **Tab/Mayús+Tab** recorren los controles.
Enter en el texto crea una línea, no programa por sí solo. Escape cancela, excepto
mientras se está guardando. Al cerrar vuelve el foco al control desde donde se
abrió, sin abrir una conversación si estabas en la lista de chats.

Dentro del gestor: **Alt+M** enfoca la lista, **Alt+E** el filtro y **Alt+D** los
detalles; **F5** actualiza y **Escape** cierra. También conserva el foco de origen.

## Persistencia y convivencia con Atajos

La cola sigue en `~/.cliente-xmpp/assistant-outbox.sqlite3`, separada de las
conversaciones. Guarda texto y destinatarios localmente sin cifrado adicional;
no borra automáticamente el historial de programaciones. No consulta servicios de IA.
El límite compartido es de 500 entradas pendientes, retenidas o en proceso.

Un único coordinador abre la cola y recupera estados al arrancar. Un envío que
quedó en curso al cerrar pasa a resultado incierto, sin repetición automática;
confirmaciones posteriores pueden resolverlo. Los reintentos específicos del
protocolo conservan el mismo ID y vuelven a comprobar autorización para los
envíos nativos; no se añade un reintento general de mensajes inciertos.

Desactivar Atajos pausa **sus** pendientes, pero no las programaciones nativas.
El diálogo no activa la API, abre un puerto ni crea una credencial. La API de
Atajos consulta/cancela únicamente sus programaciones y conserva su límite de
4000 caracteres por mensaje. Un contacto retirado o
cuya identidad cambió queda retenido, nunca se sustituye por otro destinatario.

Antes de volver a una versión anterior de CAN, cancela los pendientes nativos:
las versiones anteriores no distinguen su origen y podrían tratarlos como
pendientes de Atajos si esa integración está activa. Revertir el código no
requiere borrar la base ni las conversaciones.

## Validación manual pendiente

Con un contacto de pruebas autorizado: comprueba teclado y NVDA, cancelación,
texto largo, desconexión, cierre/reapertura, suspensión, cambio de cuenta y ambas
políticas. Verifica recepción en WhatsApp oficial; las pruebas unitarias no
demuestran entrega real. No uses conversaciones reales para pruebas destructivas.
