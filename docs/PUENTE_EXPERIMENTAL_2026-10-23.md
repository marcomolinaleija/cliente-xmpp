# Puente experimental R6: descarga manual sin actualizar el canal estable

Registro de la publicación del 23 de octubre de 2026: se publicó en GHCR la
misma imagen R6 que ejecutaba entonces el VPS, sin reconstruirla ni modificar
sus fuentes. Esta entrega es para pruebas manuales; no es
`v37` ni una actualización automática del puente local.

Esta nota y su manifiesto describen esa entrega histórica, no la imagen que
ejecuta actualmente producción. El corte posterior R7 se documenta en
`docs/INCIDENTE_PUENTE_R7_2026-10-25.md`.

## Identidad publicada

| Campo | Valor |
|---|---|
| Etiqueta | `ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v-ex-20261023-r6` |
| Digest del manifiesto | `sha256:b419138d33dcc15b94e7e4abe3837d621c29190a770f1d15831138dd45a73a63` |
| ID de imagen/configuración | `sha256:a772d3842cadcc1f72a85379b391e80190f5ddf6aebb44fae8e00d11babd51a8` |
| Plataforma | `linux/amd64` |
| Rama contrastada | `voice-calls` |
| Commit contrastado | `32f824b1a300f351e6b9b20717f1adc1ba5c4ce7` |

El commit indica las fuentes contrastadas, no un nuevo build ni una etiqueta
de procedencia añadida a la imagen. El manifiesto experimental está en
`tools/wsl-appliance/bridge-update-manifest-experimental.json`.

## Descargar para una actualización manual

```sh
docker pull ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v-ex-20261023-r6@sha256:b419138d33dcc15b94e7e4abe3837d621c29190a770f1d15831138dd45a73a63
```

La descarga no cambia ni reinicia un puente existente. La aplicación de la
imagen al entorno destinatario es un paso posterior: debe preservar su configuración,
volúmenes, permisos y respaldo. No se ha actualizado su instalación desde aquí.

El paquete permite descarga anónima: la etiqueta experimental separa el canal,
pero **no restringe el acceso a una persona destinataria concreta**.

## Aislamiento del canal estable

- `bridge-update-manifest.json` permanece en **v35**, tanto en este checkout como
  en `main` remoto. No se cambió su URL, el instalador ni `release-manifest.json`.
- El actualizador local conserva su validación de etiquetas `v` numéricas:
  acepta `v35` y rechaza `v-ex-20261023-r6`. Este JSON es un registro de entrega
  manual; **no debe pasarse a `whatsapp-can-bridge-image update`**.
- No se modificaron `v35`, `latest` ni otras etiquetas estables. No se crearon
  `v36` o `v37`, versiones del cliente Windows ni releases de GitHub.
- Al preparar esta entrega el 23 de octubre, el manifiesto y esta nota quedaron
  locales. Su inclusión posterior en Git no publica otra imagen ni cambia
  los canales de actualización.

## Evidencia de verificación de aquella entrega

- Manifest y configuración accesibles anónimamente, con sus SHA-256 verificados.
  Los tres blobs de capas responden y sus tamaños coinciden; los tres `diff_ids`
  coinciden con la imagen del VPS. No se hizo una descarga completa en otra máquina.
- 21 comprobaciones de fuentes activas, transformaciones de recuperación,
  marcadores y eco grupal coinciden con esta rama. Los archivos Go se contrastaron
  con sus transformaciones y formato correspondientes.
- Las copias históricas de vinculación bajo `/opt/linking-tools` no son las
  fuentes activas; `slidge_whatsapp/linking.py` sí coincide. Dos archivos auxiliares
  sólo difieren en líneas en blanco finales. No se reconstruyó Go ni se repitió race.
- Suite Python completa: **1 129 métodos / 642 subcasos**, sin fallos, errores ni
  omisiones. Ruff y comprobación del diff aprobados.
- **18 smokes de imagen aprobados**, aislados sin red externa, datos productivos
  ni socket Docker. El de recuperación falló primero porque el montaje temporal
  tenía `noexec`; pasó al permitir `exec` sólo para sus comandos ficticios en ese
  contenedor aislado, sin modificar la imagen.
- El contenedor productivo conserva identidad, imagen, arranque y cero reinicios;
  Compose y las identidades de los demás contenedores permanecen iguales. No se
  reconectó WhatsApp ni se enviaron mensajes de prueba.

Diagnósticos locales ignorados por Git: `.test-results/experimental-ghcr/`.
Estas comprobaciones no acreditan instalación, mensajería o llamadas reales en
el equipo destinatario, ni sustituyen una prueba humana de accesibilidad.

Referencia: [publicación y descarga por digest en GHCR](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
