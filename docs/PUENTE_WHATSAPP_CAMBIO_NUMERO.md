# Identidad grupal después de cambiar el número

La corrección evita elegir al participante propio por un apodo que puede repetirse
después de volver a vincular. No ejecuta una purga de chats o sesiones ni vuelve a
vincular WhatsApp. Conserva la identidad archivada de mensajes y citas.

## Cambios

- `tools/patch_slidge_number_change.py` modifica fuentes exactas de la base v34,
  valida todas sus anclas antes de escribir y admite una segunda aplicación sin cambios.
- El participante propio se busca por `occupant_id`: primero la identidad explícita,
  después la identidad propia de los metadatos actuales de grupos de esa sesión.
  Si no hay esa evidencia, se admite una identidad propia almacenada únicamente si
  es única; con varias identidades históricas se usa el identificador interno
  `slidge-user`. Nunca se elige la primera fila por apodo.
- La reconexión actualiza el número propio incluso si el futuro del login ya terminó.
- El adaptador Go conserva evidencia de identidad propia por PN o LID y utiliza
  los identificadores del dispositivo autenticado, no una asociación propia obsoleta.
- El historial mantiene separado el identificador del grupo del identificador del
  remitente. `FromMe` explícito permite identificar mensajes y reacciones propios
  aunque todavía contengan el número anterior.
- Una reacción histórica sin evidencia de autor no fabrica un remitente ni interrumpe
  el procesamiento del mensaje principal. Este límite no garantiza recuperar datos
  cuya identidad WhatsApp no proporciona.
- El código fuente de CAN clasifica un `Re-login failed` genérico como error de
  conexión, no como prueba de desvinculación. Los errores explícitos de sesión no
  autenticada siguen requiriendo reingreso. Este cambio del cliente requiere una
  futura distribución Windows para llegar a un ejecutable ya instalado.

## Validación y alcance

La prueba local `tests/test_patch_slidge_number_change.py` verifica el contrato del
parche y está registrada en `tests/suites.json`. Los casos Go de
`tools/bridge_number_change_test.go` prueban identidades actuales/antiguas, separación
de sala y remitente, reacciones y casos negativos. El smoke
`tools/smoke_bridge_number_change_runtime.py` usa SQLite en memoria con datos ficticios
y prueba alias históricos, aislamiento entre cuentas y reconexión.

`tools/Dockerfile.bridge-number-change-candidate` parte del digest v34 fijado, conserva
sus dependencias y recompila el adaptador nativo sin cambiar su ABI. Ejecuta pruebas
Go/race y los smokes existentes junto al nuevo. No publica imágenes por sí mismo.

Una imagen construida y sus pruebas no acreditan entrega real, funcionamiento con
NVDA ni recuperación completa del historial. Esas comprobaciones permanecen separadas.

## Operación y reversión

La tabla `participant` es una caché: el `ParticipantStore` de la base v34 ya la
vacía al arrancar y la reconstruye. Sus claves primarias no son identificadores
históricos estables. Conservar alias durante la resolución no desactiva ese
comportamiento previo; la verificación de historial debe comparar filas y contenido
de MAM, mapas de mensajes y adjuntos, no exigir que sobrevivan las claves de esa caché.

El despliegue requiere autorización. Antes de recrear únicamente `slidge-whatsapp`,
se conserva la imagen anterior y se respaldan la configuración y las cuatro bases
activas con la API SQLite de backup, con el puente detenido para mantener consistencia
entre ellas. Se verifican integridad y hashes; estos respaldos locales no equivalen
a una copia de desastre fuera del VPS ni incluyen una copia completa de adjuntos.

Se cambia únicamente la referencia de imagen del servicio. Los montajes, credenciales,
cuentas y demás contenedores deben permanecer iguales. La reversión ordinaria restaura
la imagen/configuración previa, no una base de datos antigua: restaurar datos a ciegas
podría perder mensajes posteriores. No se cambia el manifiesto estable ni se publica
en GHCR o Windows sin autorización independiente.

## Verificación de este despliegue

El 17 de octubre de 2026 se activó primero en el VPS la imagen local
`cliente-xmpp-bridge:number-change-candidate-20261018t0241z`. Tras la confirmación
humana de recepción, se publicó exactamente esa imagen, sin reconstruirla, como
`ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v35`, con digest
`sha256:5cc36c7f2d1cfc08632f6d6e88cd38c009138915069fc0de282c3a43c60de86e`.
Se verificó su lectura anónima y el mismo ImageID de la candidata probada.
Pasaron 942 métodos/425 subcasos Python, Ruff, Go/race y 11 smokes.
La comparación con el respaldo confirmó que las filas originales de MAM,
mapas de mensajes grupales y adjuntos no faltaban ni cambiaron. Los otros
31 contenedores conservaron sus identidades, imágenes, arranques y reinicios.
El usuario confirmó recepción de mensajes privados y grupales; también se correlacionó
en la base local el mensaje concreto que había reportado faltante. Esa comprobación
no acredita todas las rutas de envío, respuestas, multimedia o accesibilidad.
