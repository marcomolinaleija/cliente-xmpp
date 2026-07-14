# Notificaciones nativas de Windows

## Alcance

Esta funcionalidad pertenece exclusivamente a `cliente-xmpp`. No cambia stanzas, eventos XMPP,
la imagen de `slidge-whatsapp`, Prosody ni `marco-vps`.

El cliente usa `windows-toasts` sobre Windows Runtime para publicar notificaciones en Windows 10
y Windows 11. Registra para el usuario actual el identificador
`MarcoML.WhatsAppCAN` bajo `HKCU\SOFTWARE\Classes\AppUserModelId`; no requiere privilegios de
administrador ni un acceso directo creado a mano.

## Experiencia de usuario

La cabecera de la ventana conectada contiene el boton `Configuracion`. Abre una pantalla propia
y deja la lista de chats sin opciones adicionales. Escape o `Volver` regresan a la conversacion
o a la lista desde la que se abrio.

Opciones persistidas en `%USERPROFILE%\.cliente-xmpp\settings.json`:

- `Mostrar mensajes como notificaciones de Windows`: activada de forma predeterminada.
- `Mostrar el contenido del mensaje`: activada de forma predeterminada.
- `Anunciar tambien el mensaje directamente con NVDA`: desactivada de forma predeterminada para
  evitar que NVDA lea dos veces la notificacion nativa.
- Los dos controles de sonido que ya existian para el chat abierto y los mensajes enviados.

El boton `Probar notificacion de Windows` permite validar la integracion sin esperar un mensaje.
La apariencia, el sonido y la supresion por No molestar o Asistente de concentracion siguen la
configuracion del propio Windows.

## Reglas de entrega

Una notificacion solo se intenta cuando `MessageReceived.notify` es verdadero y el mensaje se
agrego realmente a la conversacion. Ademas:

- No se notifica un mensaje saliente.
- No se notifica un chat silenciado.
- No se muestra un toast si ese mismo chat esta abierto y la ventana esta activa.
- MAM, inbox de arranque, carbons duplicados y mensajes historicos no generan avisos.
- Si Windows no puede inicializar o mostrar el toast, se conserva el anuncio NVDA y el sonido
  anterior como respaldo.
- Cuando Windows acepta el toast, se omite el sonido propio de mensaje nuevo para no reproducir
  dos alertas. El usuario puede activar el anuncio directo adicional de NVDA desde Configuracion.

En grupos, el titulo usa `participante en nombre del grupo`. Para multimedia se usa la descripcion
normalizada del cliente, sin exponer nombres hash de adjuntos. Los titulos se limitan a 64
caracteres y el cuerpo a 256, que son los limites de la plataforma.

## Acciones

- Pulsar el cuerpo o `Responder` restaura la ventana, abre el chat exacto y enfoca el compositor.
- `Marcar como leido` baja el contador local y usa `_mark_chat_displayed`, la misma ruta XMPP que
  usa el cliente al abrir una conversacion.

Los callbacks de Windows pueden ejecutarse fuera del hilo de wx. Por eso solo llaman las entradas
de `MainWindow`, que vuelven al hilo de interfaz mediante `wx.CallAfter` antes de tocar controles.

## Archivos y responsabilidades

- `cliente_xmpp/notifications/windows.py`: registro de identidad, formato, publicacion y acciones.
- `cliente_xmpp/ui/settings_panel.py`: pantalla accesible de configuracion.
- `cliente_xmpp/config/settings.py`: defaults y persistencia.
- `cliente_xmpp/ui/main_window.py`: reglas de negocio, navegacion y retorno seguro al hilo wx.

No se deben mover objetos wx al modulo de notificaciones ni producir toasts directamente desde la
capa XMPP.

## Validacion para colaboradores

Con el entorno `XMPP`:

```powershell
python -m pip install -e .
python -m compileall cliente_xmpp tests
python -m ruff check .
python -m unittest discover -s tests -v
git diff --check
```

Prueba manual minima en Windows:

1. Conectar el cliente y abrir `Configuracion`.
2. Activar las notificaciones y pulsar `Probar notificacion de Windows`.
3. Confirmar que aparece bajo `WhatsApp CAN` en el Centro de notificaciones.
4. Recibir un mensaje con otro chat seleccionado y probar `Responder`.
5. Recibir otro mensaje y probar `Marcar como leido`.
6. Silenciar un chat y confirmar que no genera toast, sonido ni anuncio directo.
7. Mantener el chat abierto con la ventana activa y confirmar que usa el comportamiento local del
   chat, sin toast duplicado.
8. Desactivar la vista previa y confirmar que solo se muestra `Nuevo mensaje`.
9. Probar Escape, Volver, orden de foco y lectura de todas las casillas con NVDA.

Al empaquetar el cliente deben incluirse las dependencias declaradas por `windows-toasts`, en
particular sus bindings `winrt-*`. No se debe reemplazar esta ruta por PowerShell ni por polling
del puente.
