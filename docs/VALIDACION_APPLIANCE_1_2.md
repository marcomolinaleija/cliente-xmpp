# Validación de la appliance 1.2 con voz obligatoria

## Candidata con v39 — 24 de noviembre de 2026

Sustituye a la candidata de octubre, que incluía un v36 compilado localmente.
Sigue sin publicarse; el canal estable continúa en 1.1.0.

| Elemento | Valor |
| --- | --- |
| Paquete | `WhatsAppCAN-Bridge-amd64.wsl`, 751022080 bytes |
| SHA-256 del paquete | `b3d1e3c7ee31e28826799d0e7bc44a0229bb6235f64aa19d8514fe42598f321e` |
| Imagen incluida | `ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v39`, copia exacta de GHCR |
| Manifiesto | `sha256:016af7dc331cb2bd173449485e49638123814acbb497dda3e742f8f09a3fb5b6` |
| Configuración de imagen | `sha256:556d4786c2d2c38dc642a5bb42dc2bb29384cfa0823366d1e9ec764cf5323459` |
| Instalador | `install-appliance.ps1`, SHA-256 `a67401cd223698ad3b05d351c67c9d858c1466f7641bfdddcad7d4ad91c8a6a8` |

El OCI se obtuvo descargando el manifiesto y cada blob publicados y
comprobando sus digests, sin `docker save` ni retag; un blob repetido entre
capas se guarda una sola vez. Artefactos locales ignorados por Git en
`dist/wsl/appliance-1.2-v39/`, con el manifiesto candidato
`release-manifest-candidate.json`.

### Corrección de la comprobación de voz

Las imágenes publicadas desde `puente-whatsapp` (v36 a v39) no contienen
`/opt/voice-tools/smoke_voice_integration.py`: allí sólo existe en la etapa de
pruebas. La candidata de octubre lo tenía porque su v36 era un build local.
Con una imagen publicada, el build fallaba y una appliance 1.2 actualizada a
v37 o posterior rechazaba la voz.

La appliance lleva ahora su comprobación en
`/usr/local/libexec/whatsapp-can-bridge-voice-check.py` y la entrega por stdin
(`python -B -`), sin red ni montajes, tanto al aprovisionar como en
`whatsapp-can-bridge-voice`. No depende de rutas internas de la imagen.

### Validación nativa

Informe `.test-results/appliance-native/native-82a7d675.json`: los cuatro
escenarios (`clean`, `legacy`, `modern`, `rollback`) aprobados y
`completed=true`, con los paquetes públicos 1.0 (`983e3622…`) y 1.1
(`5c9efa86…`) verificados por hash. Se retiraron las distribuciones
desechables.

Cambios del validador necesarios para Windows PowerShell 5.1, que elimina las
comillas dobles de los argumentos nativos y deja `$PSScriptRoot` vacío en los
valores por defecto: carpeta de salida calculada tras el `param`, runner de
`python -c` y preparación de datos sintéticos con comillas simples. El
instalador y el actualizador no usan ese patrón.

El escenario `modern` suponía que la imagen anterior carecía de voz; la del
canal estable (v37) ya la incluye y la comprobación la acepta con razón. El
validador construye ahora, sin red, una imagen derivada sin el módulo de voz y
exige que `whatsapp-can-bridge-voice prepare` y `configure` la rechacen.

Pasaron Ruff, `git diff --check`, el área `bridge` de la suite Python y
`test-voice-configuration.sh` como root en Linux. Las cuentas son sintéticas:
no acredita una llamada real ni audio audible.

## Candidata de octubre (histórica)

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
