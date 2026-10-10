# Pruebas por capa y área

Usa el runner desde la raíz con el entorno Conda `XMPP`. No instala dependencias,
no abre CAN, no construye binarios/imágenes ni se conecta a una cuenta real.
Las integraciones locales pueden abrir puertos **loopback**, controles wx ocultos
o primitivas Windows; sus archivos y bases de datos son fixtures temporales.

## Ruta rápida

```powershell
# Iterar: capa rápida, no toda la suite
conda run -n XMPP python tools/run_tests.py

# Trabajar en historial: todas las capas de esa área
conda run -n XMPP python tools/run_tests.py --suite all --area history

# Un cambio que cruza mensajes, archivos y SQLite
conda run -n XMPP python tools/run_tests.py --suite all --area messaging --area media --area storage

# Cierre de refactor compartido / validación previa a release
conda run -n XMPP python tools/run_tests.py --suite all
conda run -n XMPP python -m ruff check .
git diff --check
```

`--suite all` ejecuta **toda la suite Python local**, no las pruebas Go/race de
una imagen del puente ni WhatsApp/NVDA reales. Un `PASS suite=fast` no es un
certificado de integración. Construir/publicar sigue requiriendo autorización.

## Estructura

La organización es **lógica**, no una mudanza de archivos: preserva los imports
y los comandos específicos de las guías existentes. El catálogo
[`tests/suites.json`](../tests/suites.json) es la fuente ejecutable de clasificación.

| Capa | Contrato | Cuándo |
|---|---|---|
| `fast` (predeterminada) | Modelos, protocolo/servicios con dobles, acciones UI sin ventanas reales y fixtures pequeños | Durante edición |
| `integration` | HTTP local grande, SQLite/paginación de volumen, wx nativo y servicios con hilos | Cambios en sus áreas y cierre completo |
| `contracts` | Compatibilidad/idempotencia de parches, configuración de appliance y catálogo de pruebas | Cambios de bridge/tooling y cierre completo |
| `all` | Unión exhaustiva de las tres, sin exclusiones ni cuota de casos | Refactor transversal y antes de release |

Áreas: `automation`, `connection`, `history`, `media`, `messaging`, `platform`,
`storage`, `bridge` y `testing`. Se unen al repetir `--area`. Los archivos mixtos
tienen excepciones por clase/método; por ejemplo, el stream de 91 MiB es integración,
pero el cálculo de deadline y el reintento con dobles son rápidos.

```powershell
# Inventario, sin ejecutar ningún caso
conda run -n XMPP python tools/run_tests.py --suite all --list

# Caso o familia identificable (substring o patrón con *)
conda run -n XMPP python tools/run_tests.py --suite all --area media --match '*retry*'

# Medir: tabla limitada a diez métodos lentos, detalles en JSON
conda run -n XMPP python tools/run_tests.py --suite all --profile
```

Un área desconocida, selección vacía, import fallido o catálogo obsoleto devuelve
un código distinto de cero. No existe un fallback que apruebe cero pruebas.
`--list` informa explícitamente **not executed**. No hay selección automática por
Git: cambios compartidos en identidad, eventos, almacenamiento, hilos, dependencias
o fixtures requieren `all`, no sólo el área sugerida por un nombre de archivo.

## Salida y evidencia

- El proceso completo escribe su salida en `.test-results/<fecha>-<capa>.log`,
  incluyendo imports, subprocesos y mensajes que no respeta el buffer de unittest.
- El JSON contiguo contiene selección exacta, métodos, subcasos explícitos,
  errores, skips, tiempos por método y tracebacks. Los detalles no se publican.
- Si pasa, lee sólo el resumen. Si falla, la consola muestra hasta tres problemas
  con tracebacks acotados; consulta el log/JSON local para el resto.
- `--profile` es diagnóstico, no el modo habitual. El tiempo global incluye
  discovery/imports y fixtures de clase; los tiempos de método incluyen sus
  `setUp`/`tearDown`, pero no `setUpClass`. No compares ambos como si fueran iguales.
- `--footprint` añade al JSON las líneas ejecutadas de funciones importadas de
  `cliente_xmpp` y `tools` usando eventos locales de Python 3.12. Sirve para comparar
  una poda, no mide porcentaje de cobertura ni ramas, módulos cargados después,
  código dinámico o nativo. Tiene sobrecoste: no lo uses al medir velocidad ni en
  cada iteración. Un mismo footprint no implica detectar los mismos errores.
- Reutilizar evidencia significa no repetir sin cambios relevantes de fuente,
  pruebas, dependencias o entorno; **no** hay caché automática que omita ejecución.
- Los resultados están ignorados por Git. No copies SQLite, logs privados,
  credenciales ni fixtures de conversaciones reales al preparar un commit.

Los comandos heredados de `unittest` siguen funcionando. Para usarlos con menos
ruido añade `-q -b`: quita progreso y descarta stdout/stderr de casos aprobados,
pero no captura necesariamente imports, handlers preexistentes o procesos hijos.
El runner captura también esas salidas a nivel de proceso y conserva el exit code.

## Añadir, sustituir o retirar pruebas

1. Describe el comportamiento y el fallo que detectaría; usa fixtures ficticios.
2. Registra el módulo en el catálogo y excepciones si mezcla capas/áreas.
3. Prueba límites, error y aislamiento además del camino feliz. Un `Mock` no
   sustituye un contrato de serialización, SQLite o control nativo.
4. Reusa **bytes inmutables** costosos; nunca comparte modelos/JSON/datos mutables
   entre casos ni cachees la salida de la función que estás probando.
5. Usa matrices con `subTest(case=...)` para variantes del mismo contrato. Conserva
   cada variante y su diagnóstico; menos métodos no significa menos escenarios.
6. Retira una comprobación sólo cuando otra la implique de forma demostrable o
   la funcionalidad haya desaparecido. Registra sustituto y motivo en la auditoría.
7. Para paginación usa suficientes filas para más de dos páginas, una última
   parcial y fechas empatadas; conserva al menos un stress real representativo.
8. Tras reorganizar, ejecuta `all`, lint y diff-check. Una reducción de tiempo o
   líneas ejecutadas por sí sola no demuestra conservar cobertura de errores.

## Puente y validación humana

Los 20 archivos Go `*_test.go` (51 funciones `Test*`) y los 30 scripts
`smoke_bridge*.py` auditados viven
en `tools/`, no en discovery de unittest. No todos son ejecutables independientes:
algunos son fixtures/helpers de otros smokes. `smoke_bridge_contact_name_data.py`
es un diagnóstico agregado sobre dos SQLite autorizados, no una prueba sintética
que deba entrar a discovery. Los Dockerfiles de la cadena del
puente copian y ejecutan sus pruebas en el runtime apropiado; la capa v31 añade
audio y conserva los smokes v30 de packs/stickers/no-auto-join. No se eliminan
por parecer repetidos: contrato de parche, binding Go y runtime son fronteras distintas.

Para imagen/WSL consulta [puente personalizado](PUENTE_PERSONALIZADO.md) y
[WSL2](PUENTE_WHATSAPP_WSL2.md). Sus builds, Go/race, pruebas vivas y efectos
operativos no se disparan desde este runner. Una prueba con archivo generado de
91 MiB verifica el transporte local, no el APK del usuario ni su entrega WhatsApp.

NVDA/JAWS, foco real, permisos multimedia y envío/recepción con una cuenta de
prueba siguen siendo gates humanos aparte. No provoques envíos ni mutaciones
de producción para obtener una marca verde.

## Evidencia de la reorganización

Consulta la [auditoría medida y decisiones de retirada](AUDITORIA_PRUEBAS_2026-10-10.md).
Su inventario JSON permite localizar el coste por módulo sin leer logs exitosos.
