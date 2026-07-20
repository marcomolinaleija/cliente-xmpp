# Guía de actualizaciones y releases de WhatsApp CAN

Esta guía describe el ciclo completo: asignar una versión, compilar, validar, publicar una
release accesible y comprobar la actualización desde una instalación anterior.

## Resumen del sistema

La distribución usa nombres estables:

```text
WhatsApp-CAN/
├── WhatsApp-CAN.exe
├── update.exe
└── _internal/
```

Dos segundos después de mostrar la ventana, el ejecutable consulta en un hilo de fondo la
release estable más reciente de GitHub. Si el tag contiene una versión mayor, muestra las notas
y pregunta al usuario. Solo después de una respuesta afirmativa inicia `update.exe`.

La comprobación se hace una vez por cada arranque, no solo durante la primera ejecución de toda
la vida de la instalación. Si el usuario responde `Ahora no`, se le puede volver a preguntar en
el siguiente arranque. Ejecutar el proyecto desde Python no consulta GitHub automáticamente.

## Versiones que deben distinguirse

### Versión de WhatsApp CAN

La versión de la aplicación debe coincidir en estos archivos:

| Archivo | Campos |
| --- | --- |
| `cliente_xmpp/__init__.py` | `__version__` |
| `pyproject.toml` | `project.version` |
| `windows_version_info.txt` | `filevers`, `prodvers`, `FileVersion` y `ProductVersion` |

`build_release.ps1` detiene el proceso si encuentra una diferencia. La comparación con GitHub
usa `cliente_xmpp.__version__`.

### Versión del actualizador

`update_version_info.txt` contiene la versión del propio `update.exe`. Es independiente de la
versión de WhatsApp CAN y no interviene en la comparación con el tag. Solo debe incrementarse
cuando cambie el actualizador y quieras identificar esa nueva compilación en las propiedades de
Windows.

`update.exe` puede quedar compilado y reutilizarse, pero el pipeline lo recompila en cada release
para incluir siempre el código vigente. Se ejecuta desde una copia en `%TEMP%`; por eso el ZIP
nuevo puede sustituir también el `update.exe` instalado.

## Formato obligatorio de una release

Para la versión `1.0.1`, la release debe ser estable y contener exactamente:

```text
Tag: v1.0.1
WhatsApp-CAN-1.0.1.zip
WhatsApp-CAN-1.0.1.zip.sha256
```

También se aceptan tags como `1.0.1` o `1.0.1-stable`. El cliente extrae y compara la parte
numérica. Ignora borradores y prereleases. La versión numérica del ZIP debe coincidir con la del
tag.

El `.sha256` contiene la huella SHA-256 del ZIP:

```text
<64 caracteres hexadecimales>  WhatsApp-CAN-1.0.1.zip
```

El actualizador no extrae ni instala nada si el hash calculado es diferente. SHA-256 comprueba
integridad, pero no reemplaza la firma de código: para distribución pública conviene firmar
`WhatsApp-CAN.exe` y `update.exe`.

## Requisito indispensable: feed público

Una release dentro de un repositorio privado también es privada. Los usuarios anónimos no pueden
consultarla ni descargar sus assets. No incrustes un token personal en el ejecutable.

Antes de distribuir la actualización elige una de estas opciones:

1. hacer público `marcomolinaleija/cliente-xmpp`; o
2. publicar los assets en otro repositorio público y cambiar `DEFAULT_RELEASE_API` antes de
   compilar; o
3. usar un feed HTTPS público compatible y configurar `WHATSAPP_CAN_UPDATE_API_URL` en un entorno
   administrado.

`WHATSAPP_CAN_GITHUB_TOKEN` existe para pruebas internas controladas, no para distribuir un token
dentro del programa.

Comprueba la visibilidad actual:

```powershell
gh repo view marcomolinaleija/cliente-xmpp --json url,isPrivate
```

Para usuarios finales, `isPrivate` debe ser `false`, salvo que el binario apunte a otro feed
público. Cambiar la visibilidad es una decisión del propietario; no la automatiza este proyecto.

## Paso 1: preparar el entorno

```powershell
conda activate XMPP
python -m pip install -e ".[build]"
gh auth status
```

El entorno necesita Python 3.12, wxPython, PyInstaller, los DLL locales de NVDA/libmpv y
`ffprobe` disponible en `PATH`, según `WhatsApp-CAN.spec`.

Cierra cualquier copia ejecutada desde `dist/WhatsApp-CAN`. El pipeline lo comprueba antes de
limpiar o reemplazar archivos.

## Paso 2: asignar la nueva versión

Ejemplo para pasar de `1.0.0` a `1.0.1`:

1. cambia `cliente_xmpp/__init__.py` a `__version__ = "1.0.1"`;
2. cambia `version = "1.0.1"` en `pyproject.toml`;
3. actualiza `windows_version_info.txt`:
   - `filevers=(1, 0, 1, 0)`;
   - `prodvers=(1, 0, 1, 0)`;
   - `FileVersion` a `1.0.1`;
   - `ProductVersion` a `1.0.1`.

No publiques otra release con la misma versión: el cliente solo ofrece una versión estrictamente
superior a la instalada.

## Paso 3: escribir notas de la release

Crea `release_notes.txt` en la raíz con cambios visibles para el usuario. Por ejemplo:

```text
WhatsApp CAN 1.0.1

- Se añadió la comprobación accesible de actualizaciones.
- Se mejoró la estabilidad del ejecutable.
- Se corrigieron errores de reproducción multimedia.
```

No incluyas credenciales, rutas privadas, detalles internos innecesarios ni tokens.

## Paso 4: compilar y validar

Ejecuta el pipeline completo, sin `-SkipChecks` para una publicación real:

```powershell
.\build_release.ps1
```

El script realiza, en este orden:

1. comprueba que la distribución no esté abierta;
2. valida todas las declaraciones de versión;
3. ejecuta `compileall`, Ruff y todos los tests;
4. compila `update.py` como `dist/update.exe` one-file;
5. compila WhatsApp CAN como aplicación onedir;
6. copia `update.exe` junto a `WhatsApp-CAN.exe`;
7. genera `release/WhatsApp-CAN-<versión>.zip`;
8. calcula `release/WhatsApp-CAN-<versión>.zip.sha256`;
9. vuelve a verificar el hash;
10. extrae el ZIP con el mismo código seguro del actualizador y comprueba
    `WhatsApp-CAN.exe`, `update.exe` y `_internal`;
11. compila con Inno Setup el instalador por usuario a partir de la carpeta `onedir`;
12. genera el SHA-256 del instalador.

`-SkipChecks` solo sirve para iteraciones locales después de una validación completa; no debe
usarse para el build que se publicará.

## Paso 5: revisar los archivos resultantes

Para `1.0.1` deben existir:

```text
dist/WhatsApp-CAN/WhatsApp-CAN.exe
dist/WhatsApp-CAN/update.exe
release/WhatsApp-CAN-1.0.1.zip
release/WhatsApp-CAN-1.0.1.zip.sha256
release/WhatsApp-CAN-1.0.1-Setup.exe
release/WhatsApp-CAN-1.0.1-Setup.exe.sha256
```

El instalador usa `%LOCALAPPDATA%\Programs\WhatsApp CAN`, crea un acceso en el menú Inicio y
ofrece, sin marcarla por defecto, la opción de crear un acceso directo en el escritorio. Esta
instalación por usuario evita pedir permisos de administrador y permite que el actualizador
sustituya la carpeta completa de la aplicación.

Puedes repetir la validación manualmente:

```powershell
python tools\validate_release.py `
  release\WhatsApp-CAN-1.0.1.zip `
  release\WhatsApp-CAN-1.0.1.zip.sha256
```

## Paso 6: publicar una release estable

Confirma primero que el repositorio o feed elegido sea público. El publicador recomendado es
`publish_release.bat`; usa la sesión ya iniciada de GitHub CLI y no guarda tokens ni contraseñas.

Flujo interactivo:

```bat
publish_release.bat
```

Flujo con argumentos, conservando la confirmación final:

```bat
publish_release.bat 1.0.5 zip
publish_release.bat 1.0.5 installer
```

El modo `zip` publica `WhatsApp-CAN-<versión>.zip` y su `.sha256`. El modo `installer` añade el
instalador `Setup.exe` y su propio `.sha256`. Se puede agregar `--yes` como tercer argumento para
automatización; esto omite únicamente la confirmación y nunca las validaciones.

Antes de crear nada, el script:

1. comprueba que las tres declaraciones de versión coincidan;
2. exige el ZIP y su firma dentro de `release/`; si faltan, pide ejecutar `build_release.ps1`;
3. valida el ZIP con `tools/validate_release.py` y también verifica la firma del instalador cuando
   se incluye;
4. comprueba que `release_notes.txt` exista y no esté vacío;
5. exige un árbol Git limpio y que la rama local coincida exactamente con su rama remota;
6. rechaza un tag o una release que ya existan;
7. muestra las notas y pide confirmación;
8. crea y sube el tag `v<versión>`, publica la release estable y verifica sus assets.

Si se necesita realizar el proceso manualmente, el equivalente mínimo es:

```powershell
$version = "1.0.1"
$tag = "v$version"

gh release create $tag `
  "release\WhatsApp-CAN-$version.zip" `
  "release\WhatsApp-CAN-$version.zip.sha256" `
  --repo marcomolinaleija/cliente-xmpp `
  --title "WhatsApp CAN $version" `
  --notes-file release_notes.txt `
  --latest
```

No uses `--draft` ni `--prerelease` para el canal estable. No publiques solo el ZIP: el cliente
rechaza una release sin el `.sha256` exacto.

## Paso 7: verificar la publicación

No des por terminada la subida solo porque `gh release create` regresó. Verifica metadatos y
assets:

```powershell
gh release view v1.0.1 `
  --repo marcomolinaleija/cliente-xmpp `
  --json tagName,isDraft,isPrerelease,url,assets,publishedAt
```

Confirma que:

- `tagName` sea `v1.0.1`;
- `isDraft` e `isPrerelease` sean `false`;
- aparezcan el ZIP y su `.sha256`;
- los nombres tengan exactamente la misma versión;
- la URL pueda abrirse sin una sesión autenticada.

La última comprobación debe hacerse en una ventana privada o desde un equipo sin sesión de
GitHub. Un release que solo funciona con la sesión del mantenedor no sirve para el actualizador
público.

## Paso 8: probar la actualización real

Conserva una copia de la versión anterior, por ejemplo `1.0.0`, y publica `1.0.1`:

1. abre `WhatsApp-CAN.exe` de la instalación `1.0.0`;
2. espera la consulta que ocurre dos segundos después de mostrar la ventana;
3. comprueba que el diálogo anuncie `1.0.1` y muestre las notas;
4. responde `Sí, actualizar`;
5. acepta UAC únicamente si la carpeta instalada requiere permisos elevados;
6. comprueba descarga, validación, cierre y relanzamiento;
7. revisa que la aplicación abra con versión `1.0.1`;
8. confirma que ajustes, credenciales y base SQLite sigan presentes;
9. ante un fallo, revisa `%LOCALAPPDATA%\WhatsApp CAN\logs\update.log`.

El actualizador intenta escribir sin elevar. Solo usa `runas` y muestra UAC cuando el directorio
padre de la instalación no es escribible, como suele ocurrir bajo `Program Files`.

## Qué protege el proceso

- Solo admite URLs HTTPS.
- Requiere SHA-256.
- Limita tamaño de descarga, extracción y número de entradas.
- Rechaza traversal, enlaces, rutas NTFS peligrosas y duplicadas.
- Ejecuta una copia temporal del updater para poder reemplazarlo.
- Prepara una carpeta staging en el mismo volumen.
- Renombra la instalación anterior como backup durante el intercambio.
- Restaura el backup si falla el cambio de carpetas.
- Mantiene ajustes y SQLite fuera del directorio actualizado.

El rollback protege el intercambio de archivos; no conserva el backup después de un relanzamiento
exitoso para comprobar la salud funcional de la nueva versión.

## Cierre de una release

Después de verificar la actualización:

```powershell
git status --short
gh release view v1.0.1 --repo marcomolinaleija/cliente-xmpp
```

Conserva en Git solamente fuentes, specs, scripts, tests y documentación. `build/`, `dist/` y
`release/` son artefactos reproducibles e ignorados por Git.
