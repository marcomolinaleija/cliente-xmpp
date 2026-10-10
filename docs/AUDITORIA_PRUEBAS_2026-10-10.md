# Auditoría y optimización de pruebas — 10 de octubre de 2026

**Resultado:** iteración rápida de 506 métodos en **4,010 s de mediana**, frente
a 792 métodos / 118,028 s de la pasada original completa: **96,6 % menos tiempo**
y **36,1 % menos métodos seleccionados** en la iteración. No son gates equivalentes:
integración y contratos siguen obligatorios al cierre de cambios transversales/release.

La reorganización también reduce el coste de fixtures sin suprimir escenarios.
No se impuso una cuota de menos de 700: la cantidad de métodos no demuestra
redundancia. La completa tiene **800 métodos** (788 anteriores reorganizados +
12 guardas nuevas del runner); conserva 169 subcasos explícitos y pasó sin
fallos, errores ni skips. No se cambia código productivo de `cliente_xmpp`.

## Alcance y evidencia

- Estado inicial: `1b98bd2`, Windows, Python 3.12.13, Conda `XMPP`.
- Inventariados todos los 65 módulos originales y el nuevo módulo del runner;
  clasificación exhaustiva por capa/área y comprobación de partición sin duplicados.
- Revisados también 20 archivos Go (51 funciones `Test*`), 30 smokes/helpers/probes
  Python y sus referencias en Dockerfiles; no mezclados con discovery del cliente.
- Inventario, importaciones de propietarios, decisiones por módulo, muestras y
  hashes de fuentes: [evidencia JSON](AUDITORIA_PRUEBAS_2026-10-10.json).
- Guía ejecutable: [PRUEBAS.md](PRUEBAS.md); política resumida en `AGENTS.md`;
  `build_release.ps1` conserva el gate `all`, Ruff, errores y `SkipChecks` explícito.

## Medición: separar selección, velocidad y ruido

| Medida | Antes | Ahora | Interpretación |
|---|---:|---:|---|
| Iteración por defecto | Completa: 792 / 118,028 s | Rápida: 506 / mediana 4,010 s | −96,6 % de tiempo, cambia el alcance |
| Muestras rápidas aisladas | — | 4,150 / 3,409 / 4,010 s | Tres ejecuciones secuenciales, 0 fallos |
| Historial: mismos 3 contratos | Mediana 4,556 s | Mediana 2,660 s | −41,6 %, tres pares AB/BA |
| Packs salientes: mismos 3 contratos | Mediana 0,849 s | Mediana 0,780 s | −8,1 %, tres pares AB/BA |
| Preparación de archivo sticker | 9 generaciones por clase | 1 | −88,9 % de generaciones; 9 llamadas al normalizador conservadas |
| Salida visible exitosa | 2.026 bytes de salida Python original | 214 bytes de resumen del runner | −89,4 % de bytes; no es una medición de tokens |
| Gate completo al cierre | Baseline: 118,028 s, una muestra | 800 / 59,695 s, 0 fallos | No se promete mejora global estable |

Las completas optimizadas dieron 70,012 s (799 métodos, snapshot anterior),
124,244 s (800, muestra de benchmark) y 59,695 s (800, cierre tras revisar anclajes
semánticos). La ejecución simultánea de dos suites se descartó como
benchmark; los pares y muestras rápidas se corrieron sin otra suite propia activa.
Las imágenes, filesystem y controles nativos muestran variación fuerte. No sería
honesto seleccionar sólo la más rápida y anunciar una aceleración global garantizada.

Los pares usan las clases originales recuperadas en memoria de `git show
1b98bd2:tests/...` y las actuales, sin checkout/revert ni cambio de fuente productiva.
Alternan antes/después, después/antes, antes/después; los **36 métodos ejecutados**
pasaron. Datos crudos y medianas permanecen en el JSON, no sólo el mejor resultado.

## Qué se retiró y por qué

| Retirada/reestructura | Comprobación conservada más fuerte |
|---|---|
| `test_documentation_html_is_packaged_with_the_client` | El test de apertura verifica `LaunchDefaultBrowser`; el código sólo llega allí tras `is_file()`. El test eliminado tampoco probaba el paquete construido. |
| `test_cached_conversation_message_limit_is_bounded_to_five_hundred` | Test existente enriquecido: consulta real del coordinador con `limit=500`, una sola lectura por cuenta/chat y recarga al cambiar cuenta. Más útil que fijar una constante aislada. |
| 5 comparaciones contra bloques `NEW_...` importados en nombres | La segunda aplicación verificada sólo devuelve `False` si `new_source` completo está en el archivo (`patch_source`); ya prueba integridad/idempotencia. Se conservan anclajes semánticos literales, ausencia de `OLD` y rechazo. |
| Ninguna retirada de anclajes MIME/PTT | La idempotencia del parche PTT sólo busca un marker; **no** implica integridad del bloque. Se conservan bloque completo, MIME/payload literales y ausencia de conversión. |
| 3 métodos de fechas de presencia → 1 matriz | Hoy, ayer y fecha absoluta conservan exactamente valores esperados y diagnóstico `subTest(case=...)`; no se presentan como escenarios eliminados. |
| ALL de historial: 700 filas antiguas → 201 | 500 cacheadas + 201 históricas: tres páginas, última parcial, empates, drenado local, transición remota y parada vacía. El numérico 650 y stress nativo de 5.100 filas permanecen. |
| Archivo de sticker preparado repetidamente | Bytes de entrada inmutables por clase; cada mutación abre ZIP y JSON nuevos. No se cachea el resultado del normalizador bajo prueba. |

No se eliminaron los dos cuerpos AST idénticos de rechazo de parches de nombres
y PTT: sus imports enlazan a implementaciones distintas. Tampoco se eliminó por
"no tener assert" una prueba que verifica estados mediante un helper HTTP o
`self.fail` en una llamada prohibida. AST, número de asserts y antigüedad son
señales para revisar, no una autorización automática para borrar.

La revisión corrigió una candidata de poda: no basta con que un substring esté
contenido hoy en una constante `NEW_...` importada del propio código bajo prueba.
Si cambia esa constante, cambia también el esperado del test y puede ocultar
errores. Dos mutaciones dirigidas, con import frío simulado, comprobaron que
`refresh=True → False` y MIME original → `application/octet-stream` producen
**fallo de aserción, no error de setup**. Se restauran/conservan esos anclajes;
no se anuncia un score de mutaciones de toda la suite a partir de dos probes.

## Preservación y límites

- Comparación optativa de líneas de funciones Python: **10.991 antes → 11.013
  después**, en los mismos 60 archivos de `cliente_xmpp`; **ninguna línea previa
  observada desaparece**. Las 22 adicionales incluyen el contrato reforzado de caché.
- Esa medición precede la revisión final de asserts de parches; desde ella no
  cambió producción ni ningún módulo de pruebas que importe el cliente. La
  revisión posterior sólo afecta contratos de tools, comprobados focalmente y
  con otro cierre completo de 800 métodos.
- Es footprint de funciones importadas con eventos locales de Python 3.12,
  **no** porcentaje de cobertura, cobertura de ramas, nativo o prueba de mutaciones.
  Puede conservar líneas y perder sensibilidad a errores: se combina con la
  implicación lógica de asserts y preservación explícita de límites/escenarios.
- `coverage.py` no estaba instalado; no se añadió una dependencia sólo para esta
  auditoría. Un intento de instrumentar imports tuvo sobrecoste excesivo y se
  canceló; no se usó su tiempo como evidencia de velocidad.
- HTTP 91 MiB, errores/cancelación/reintento, identidad, SQLite, foco nativo,
  perfiles y aislamiento siguen en `all`. El gate del runner se verifica con
  selección vacía, import fallido, catálogo obsoleto, subcaso fallido, salida
  acotada, exit code y limpieza de instrumentación, no sólo con caminos felices.
- Go/race y smokes v31 que repiten rutas v30 no son redundantes: v31 modifica
  el adaptador/DTO compartido; probar el padre no prueba el hijo tras el parche.
  El probe de nombres lee SQLite autorizado en modo `ro`; no es un test offline.
- No se ejecutaron build, Go/race, imagen/WSL/producción, WhatsApp/APK real ni
  NVDA. No hubo instalación, commit, push ni despliegue.

## Decisión de mantenimiento

Mantener los archivos actuales evita romper imports y guías de contratos.
`tests/suites.json` aporta una estructura lógica explícita por capa y área, con
excepciones para archivos mixtos. Una prueba nueva sin registrar, selector
obsoleto o selección vacía falla. El diagnóstico queda en `.test-results/`
ignorado; no se vuelven a leer/pegar salidas exitosas en cada iteración.

Para repetir la comparación de tiempos usa `--profile` y el JSON del runner;
para comparar líneas conocidas usa `--footprint` por separado. No compares tiempos
instrumentados ni tomes una pasada rápida como toda la cobertura del proyecto.

## Inventario completo

La evidencia JSON registra **cada módulo**, sus capas/áreas, importaciones de
propietarios, coste base de métodos y motivo de retención/poda. También registra
cada fuente Go/smoke/probe y referencias en los Dockerfiles. Los tiempos por
método no incluyen `setUpClass`; los counts no incluyen todos los caminos
implícitos de loops. Para consultar sin leer el artefacto entero:

```powershell
$audit = Get-Content -Raw -LiteralPath docs/AUDITORIA_PRUEBAS_2026-10-10.json | ConvertFrom-Json
$audit.modules | Sort-Object baseline_method_seconds -Descending |
    Select-Object -First 10 path, before_methods, after_methods, baseline_method_seconds, decision
```
