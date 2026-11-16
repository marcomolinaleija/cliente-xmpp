# Validación de la appliance 1.2 con voz obligatoria

Candidata local del 21 de octubre de 2026. Sustituye la candidata anterior con
activación opcional: voz se configura automáticamente, permanece habilitada al
reiniciar y no permite `voice disable`. El micrófono sólo se abre al llamar o
contestar. El canal estable sigue en 1.1.0; no se ha publicado esta candidata.

## Identidad

| Elemento | Valor |
| --- | --- |
| Paquete | `WhatsAppCAN-Bridge-amd64.wsl`, 791193600 bytes |
| SHA-256 del paquete | `35a9936455cdebd27dc315c31d300638edcd702b8120e5c4352976e32618c27c` |
| Imagen incluida | `ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v36`, referencia local |
| Manifiesto OCI | `sha256:52dfe1497426320be10eb7d52f02585697b63efc7ca39d8f4086ddf84f0fa651` |
| Configuración de imagen | `sha256:0d068845c31c458f7653bb0f96a4cd12b4c121b854f8c27da969a88e9a9c5b1a` |
| Capacidades | `voice_required=true`, `voice_configuration=2`, `bridge_voice=true` |

Los artefactos están en `dist/wsl/appliance-1.2-voice-required/`. Son archivos
locales ignorados por Git. Las URL del manifiesto candidato sólo funcionarán
después de publicar los assets; el paquete anterior de activación opcional
no debe utilizarse como candidata actual.

La auditoría comprobó los diez archivos del overlay contra el paquete, su
versión y bundle y los bytes del instalador contra las fuentes actuales. El
paquete genérico no contiene credenciales, CA privada de instalación, entorno
configurado, sesión ni adjuntos de usuario. El preparador y el publicador
rechazan una entrega 1.2 sin motor y activación obligatoria.

## Validación

| Escenario en WSL desechable | Resultado |
| --- | --- |
| Instalación limpia | Aprobado: voz y TLS antes de cualquier comando de reparación, reinicio y nueva comprobación; rechazo de desactivación; XMPP y adjuntos |
| Migración desde la release 1.0 | Aprobado: entorno anterior con voz apagada pasa automáticamente a activa; credenciales, CA, archivos sintéticos y opciones conservados; XMPP, adjuntos y TLS |
| Migración desde 1.1 con contenedor v35 | Aprobado: respaldo OCI, adopción de v36 y voz activa; conserva una imagen compatible con contador posterior; rechaza configurar otra sin motor y comprueba la recuperación del servicio compatible |
| Rollback forzado | Aprobado: rechazo deliberado tras arrancar la candidata, exportación del estado fallido con checksum y restauración de 1.1 con datos, credenciales, XMPP y adjuntos verificados |

El validador usa el mismo paquete en los cuatro escenarios y exige que voz
funcione sin `voice enable`. Informe nativo completo y aprobado:
`.test-results/appliance-native/native-05236ba4.json`.
Un escenario incompleto no se cuenta como aprobado.

`validation-summary.json`, junto al paquete, vincula el resultado con su SHA-256,
las fuentes actuales y la suite Python. Se retiraron las cuatro distribuciones
desechables y el builder; las tres distribuciones originales quedaron intactas
y detenidas.

La suite Python completa pasó con **1086 métodos y 576 subcasos**, sin fallos,
errores ni omisiones: `20261021T142201268398Z-all.json`. Pasaron Ruff, sintaxis
Windows PowerShell 5.1 de 17 scripts, enlaces locales y `git diff --check`.
Pasaron las pruebas Linux de TLS, permisos, idempotencia, conservación del
entorno, actualización y rollback. Una activación fallida conserva voz
habilitada y comunica el fallo, sin degradarla silenciosamente a mensajería.

Se corrigió una comprobación dependiente del orden de hilos: ahora confirma
un paquete permitido antes de revocar y comprueba que otro posterior no llega,
tanto al cerrar sesión como al cambiar de dispositivo. Conserva ambos escenarios
y el control contra acceso a sesiones desde el hilo incorrecto.

La imagen nativa no cambió. Se conservaron sus pruebas Go normales y con race:
81 casos de vinculación y 86 de recepción por modalidad, con dos omisiones
conocidas del upstream en cada ejecución. También sus smokes de binding/voz,
callbacks e historial MUC; el build nuevo volvió a comprobar el binding de voz
sin cuenta ni red externa.

## Alcance

Las cuentas y los datos de pruebas son sintéticos, sin WhatsApp vinculado. TLS
y los smokes no acreditan audio audible. No se comprobó una llamada con una
persona, calidad en redes reales ni NVDA con una llamada activa.

La publicación de la imagen exacta, los assets y la promoción del manifiesto
estable siguen siendo pasos separados. Commit, push, tags y publicaciones
requieren autorización independiente.

Consulta la [guía de actualización](ACTUALIZACION_LLAMADAS.md) y el
[procedimiento WSL2](PUENTE_WHATSAPP_WSL2.md).
