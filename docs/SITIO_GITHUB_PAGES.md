# Publicar el sitio con GitHub Pages

El sitio estático vive en `docs/` y no requiere compilación, dependencias web ni analítica.
GitHub Pages debe publicar la carpeta `/docs` de la rama `main`.

## Datos pendientes antes de publicar

No actives el sitio como versión definitiva hasta completar:

1. nombre legal del responsable;
2. domicilio para efectos de privacidad;
3. correo privado para solicitudes ARCO y soporte sensible;
4. licencia del repositorio público;
5. revisión de los plazos reales de retención del VPS, Prosody, Slidge, adjuntos y logs;
6. primera release estable con sus archivos ZIP y SHA-256.

Busca `[pendiente de completar]` en `privacidad.html` para localizar los campos legales.

## Activación inicial

Después de fusionar los archivos a `main`, hacer público el repositorio y completar los datos
anteriores:

```powershell
gh api --method POST `
  repos/marcomolinaleija/cliente-xmpp/pages `
  -f 'source[branch]=main' `
  -f 'source[path]=/docs'
```

La URL inicial será:

```text
https://marcomolinaleija.github.io/cliente-xmpp/
```

Si Pages ya existe y solo necesitas corregir la fuente:

```powershell
gh api --method PUT `
  repos/marcomolinaleija/cliente-xmpp/pages `
  -f 'source[branch]=main' `
  -f 'source[path]=/docs'
```

## Dominio personalizado

Cuando el dominio esté decidido, configúralo primero en GitHub Pages y después crea el registro
DNS correspondiente. Para un subdominio, usa un `CNAME` que apunte a
`marcomolinaleija.github.io`. Añade después un archivo `docs/CNAME` con únicamente el hostname.

No uses un dominio provisional en `CNAME`: si el sitio se desactiva pero el DNS permanece
apuntando a GitHub Pages, existe riesgo de toma del subdominio.

## Descarga de la última versión

La landing enlaza a:

```text
https://github.com/marcomolinaleija/cliente-xmpp/releases/latest
```

GitHub redirige esa ruta a la release marcada como más reciente. El actualizador de la aplicación
consulta la misma release mediante la API y exige el contrato de archivos documentado en
`ACTUALIZACIONES.md`.

## Comprobación posterior

1. Abre inicio, privacidad y términos con teclado.
2. Confirma que el enlace de descarga abre la release correcta sin iniciar sesión.
3. Abre el formulario de acceso y verifica que no pide teléfono ni credenciales.
4. Comprueba la página en Windows con NVDA y a 200 % de zoom.
5. Verifica que HTTPS esté activado antes de anunciar el sitio.
