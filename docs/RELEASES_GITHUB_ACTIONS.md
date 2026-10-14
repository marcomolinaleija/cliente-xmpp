# Actualizaciones Windows por etiquetas

El workflow `.github/workflows/release-windows.yml` arranca al subir una etiqueta
nueva del cliente con formato `vX.Y.Z`, por ejemplo `v1.4.20`. No arranca por un
push normal a una rama ni por etiquetas del puente como `v33`.

Antes de crear una etiqueta, conserva las validaciones y autorizaciones de
`AGENTS.md`: revisa datos personales, confirma versiones y notas, y publica el
commit en `main`. La etiqueta debe coincidir con `cliente_xmpp/__init__.py`,
`pyproject.toml`, los cuatro campos de `windows_version_info.txt` y el primer
título de `release_notes.txt`. El workflow no aumenta versiones ni escribe commits.

## Qué hace

1. Verifica la etiqueta, el commit, las versiones, el árbol limpio y las notas.
   Rechaza releases existentes, incluso borradores, y versiones que harían
   retroceder la actualización estable.
2. Crea un entorno Conda `XMPP` temporal en Windows Server 2022, con Python 3.12,
   las dependencias del proyecto y FFmpeg/FFprobe.
3. Recupera únicamente las DLL de libmpv y NVDA de la release pública `v1.4.19`.
   `tools/ci/windows-runtime.json` fija el SHA-256 del ZIP y de ambas DLL. Estas DLL
   siguen fuera del repositorio; una copia existente distinta no se sobrescribe.
4. Ejecuta `build_release.ps1` con todos sus checks, incluida la suite Python
   completa, Ruff, versiones, paquete final y checksums. El ZIP debe contener
   libmpv, `nvdaControllerClient64.dll` y el ejecutable FFmpeg; si falta alguno
   o está vacío, no se publica.
5. Extrae el ZIP generado y ejecuta `WhatsApp-CAN.exe --smoke-test` con un perfil
   temporal nuevo. El proceso recibe un PATH limitado a Windows, sin Python,
   Conda, FFmpeg del sistema ni credenciales del workflow. Crea la ventana principal
   y procesa el bucle wx sin mostrarla; construye el selector de emojis, consulta
   SQLite y comprueba los recursos. Ejecuta el FFmpeg empaquetado, inicializa
   libmpv con salidas nulas y carga la DLL de NVDA. No conecta a XMPP, inicia el
   puente, consulta actualizaciones, programa mensajes ni lee contraseñas.
   Exige una salida limpia en menos de 90 segundos y un informe completo de la
   versión compilada. Guarda el diagnóstico JSON como artefacto de Actions durante
   siete días, también ante un fallo. Si falla, no continúa la publicación.
6. Otro job, con permiso de publicación, recupera los artefactos y vuelve a
   comprobar que la etiqueta remota sigue señalando al commit compilado. Instala
   la dependencia wxPython declarada en `pyproject.toml` porque el validador
   reutiliza la extracción y la detección del paquete de `update.py`.
7. Crea la release como borrador, descarga los assets y compara todos sus bytes
   mediante SHA-256. Sólo entonces publica y marca la release como `latest`.

El modo predeterminado es `installer`: genera el ZIP, su checksum y
`WhatsApp-CAN-<versión>-Setup.exe` con su checksum. Usa `build_release.ps1`, que
compila el script existente `installer/WhatsApp-CAN.iss` mediante Inno Setup;
se conservan sus opciones local/remoto y el manifiesto WSL. Inno Setup ya forma
parte del runner. No se reconstruye ni actualiza el puente.

Para una actualización sólo con ZIP, configura la variable de repositorio
`WINDOWS_RELEASE_MODE=zip`, en **Settings → Secrets and variables → Actions →
Variables**. La prueba de ejecución se aplica al cliente del ZIP en ambos modos;
no ejecuta el instalador ni instala WSL.

FFmpeg se distribuye dentro de `imageio_ffmpeg/binaries`, mediante el hook de
PyInstaller para `imageio-ffmpeg`. El usuario no necesita instalar FFmpeg ni
Python. FFprobe se instala en el entorno de compilación, pero no forma parte del
ZIP: el cliente no lo utiliza. La DLL de NVDA permite comunicarse con una copia
de NVDA instalada y ejecutándose; el lector de pantalla no se incluye en CAN.

El ZIP conserva una carpeta raíz `WhatsApp-CAN/`, con `WhatsApp-CAN.exe` y
`update.exe` al mismo nivel. Las DLL están en `_internal/cliente_xmpp/lib/` y
FFmpeg en `_internal/imageio_ffmpeg/binaries/`. El actualizador detecta esa raíz
y copia su contenido a la carpeta de instalación conservando las rutas, sin
añadir otra carpeta `WhatsApp-CAN` dentro. Se publican juntos
`WhatsApp-CAN-<versión>.zip` y `WhatsApp-CAN-<versión>.zip.sha256`, con los nombres
que reconoce el cliente al consultar la release estable `latest`.

Las acciones externas están fijadas por SHA. El job de compilación sólo puede
leer el repositorio; el job de publicación recibe `contents: write`. La publicación
usa el `GITHUB_TOKEN` temporal, sin PAT, contraseña ni dirección de correo en YAML.

## Avisos privados por correo

En tu cuenta de GitHub, abre **Settings → Notifications → System → Actions** y
elige **Email**. Para recibir tanto éxito como fallo, desactiva **Only notify for
failed workflows**. Sigue el repositorio con **Watch** y comprueba que tu correo
esté verificado. GitHub notifica las ejecuciones que hayas desencadenado; cada
colaborador que suba etiquetas debe configurar sus propios avisos.

El correo es una preferencia de cuenta, no del workflow. No se guarda su dirección
en el repositorio. El YAML, los logs y los artefactos de Actions del repositorio
público siguen teniendo la visibilidad de GitHub; no deben contener datos privados.

Referencia: https://docs.github.com/en/subscriptions-and-notifications/how-tos/managing-github-actions-notifications

## Avisos a varios chats de Telegram

Los avisos de progreso muestran la fase actual: validación inicial, entorno,
DLL, pruebas y Ruff, actualizador, cliente, ZIP, instalador, ejecución del ZIP,
artefactos, validación de publicación, borrador, comprobación de descargas y
publicación estable. Cada aviso incluye la versión y el enlace a la ejecución,
sin porcentajes ni tiempos estimados.

`build_release.ps1` emite los avisos internos sólo si
`TELEGRAM_PROGRESS_ENABLED=1`, como configura Actions. Las pruebas se ejecutan
sin token, IDs ni progreso habilitado; sus fixtures no pueden mandar avisos
reales. Los avisos intermedios son de mejor esfuerzo: un fallo de Telegram no
modifica el código de salida del trabajo ni interrumpe la compilación/publicación.

El job `notify_telegram` espera a compilación y publicación y avisa de éxito,
fallo o cancelación a cada destino. Incluye la etiqueta y el enlace a la ejecución;
sólo incluye el enlace de descarga cuando la publicación termina correctamente.
Si falla, añade la última fase alcanzada. El registro de fase continúa aunque
Telegram no acepte un aviso intermedio.
Puede complementar los avisos de correo de GitHub.

En **Settings → Secrets and variables → Actions → Secrets**, añade:

- `TELEGRAM_BOT_TOKEN`: el token del bot creado con BotFather.
- `TELEGRAM_CHAT_IDS`: los dos IDs numéricos separados por coma o por línea.
  También admite más destinatarios y elimina duplicados. No uses nombres ni teléfonos.

Cada persona debe iniciar un chat con el bot para recibir mensajes privados.
Guarda los valores directamente en GitHub, no en el YAML, un commit ni el chat
con Codex. El helper usa los secrets como variables de entorno y no imprime el
token, los IDs, las respuestas de Telegram ni las URLs que contienen el token.

Si faltan los dos secrets, no se envían avisos; si sólo hay uno o
son inválidos, el job de notificación falla. Si un chat rechaza el aviso, se
intenta avisar a los demás y se indica únicamente la posición del destino que
falló. Un fallo de Telegram no deshace una release ya publicada, pero el job
aparece fallido para poder revisarlo. Las pruebas usan una API simulada y no
envían mensajes reales.

Para probar los secrets sin compilar ni publicar, ejecuta el workflow manual
**Probar notificaciones Telegram** desde Actions. Envía un mensaje identificado
como prueba a todos los chats configurados y falla si falta la configuración o
algún destinatario no acepta el envío. Necesita que `test-telegram.yml` y el helper
estén publicados en la rama predeterminada.
El parámetro `recipient` permite seleccionar `all`, `1` o `2`, según el orden de
los IDs en el secret, para repetir sólo un destino que haya fallado. Los errores
de Telegram se clasifican en mensajes fijos con su código HTTP; no se imprime
la respuesta original ni información del destinatario.

El input opcional `identity_public_key` permite consultar el nombre de un único
chat mediante `getChat`, sin mandar un mensaje. Debe ser una clave pública RSA
PEM de al menos 2048 bits; el nombre se devuelve cifrado con OAEP/SHA-256 y sólo
puede leerse con la clave privada que se conserva localmente. No envíes la clave
privada al workflow ni publiques el resultado descifrado en commits o logs.

Referencias: https://core.telegram.org/bots/api#sendmessage y
https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets

## Fallos y comprobación real

Si fallan las pruebas o la compilación, no se crea una release. Si falla la
verificación de los assets, se conserva un borrador y la release estable anterior.
No se borra ni mueve la etiqueta. Revisa el borrador antes de volver a intentarlo:
el workflow rechaza sustituir una release existente y no aplica `--clobber`.

La primera ejecución real exige que el workflow y los helpers estén en el commit
etiquetado. Las pruebas locales de los helpers no sustituyen ese primer build en
GitHub. El smoke del ZIP no sustituye la instalación del Setup, reproducción o
grabación con dispositivos reales, la lectura con NVDA ni una conexión WhatsApp
real. NVDA no está instalado en el runner: se verifica la carga de su DLL, sin
anuncios de voz.
