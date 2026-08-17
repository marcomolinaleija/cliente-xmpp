# Appliance local de WhatsApp CAN 1.1.0

Esta versión permite actualizar el puente de WhatsApp posteriormente sin reemplazar toda la
distribución WSL2.

- Incluye el puente de WhatsApp v19 fijado por digest.
- Añade envío y descarga XEP-0363 con un máximo de 200 MiB por archivo.
- Conserva Ubuntu 24.04, Prosody, nginx, Podman y los servicios limitados a `127.0.0.1`.
- Migra automáticamente la distribución 1.0 conservando sesión, certificados, adjuntos y Prosody.
- Crea respaldos con SHA-256 antes del reemplazo y restaura la distribución anterior si falla.
- Incluye `actualizar-puente-local.ps1` para quienes ya tienen la distribución antigua; funciona
  como archivo descargado y directamente mediante `irm URL | iex` sin corromper los acentos ni
  cerrar la consola, y conserva un transcript local para diagnóstico.

Requiere Windows x64 y WSL 2.4.4 o posterior.
