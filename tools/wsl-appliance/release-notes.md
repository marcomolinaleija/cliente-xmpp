# Appliance local de WhatsApp CAN 1.2.0 — preparación

Actualización completa por versión desde las appliances 1.0 y 1.1, con preparación
automática y obligatoria para llamadas de voz. Estas notas describen las fuentes; la release y
el paquete `.wsl` requieren construcción, validación y publicación independientes.

- Exige incluir el motor de voz en el paquete mediante un bundle OCI verificado.
  La migración respalda la imagen activa y adopta la incluida sólo si es más
  reciente. Conserva una imagen instalada de igual versión o posterior.
- El actualizador consulta un manifiesto con versión, tamaño y hashes del `.wsl`
  y del instalador. Omite versiones idénticas completas y rechaza downgrades.
- Prepara WSS local en `127.0.0.1:8766`, con certificado propio firmado por la CA
  de la instalación. La clave de la CA no se monta en el contenedor.
- Voz queda habilitada automáticamente al instalar y migrar, y permanece activa
  tras reiniciar. No requiere `voice enable` ni permite `voice disable`.
- Una imagen incompatible o un fallo de WSS impide completar la nueva appliance;
  la migración recupera la anterior en lugar de entregar llamadas desactivadas.
- Conserva XEP-0363 y su máximo de 200 MiB por archivo.
- Conserva Ubuntu 24.04, Prosody, nginx, Podman y los servicios limitados a `127.0.0.1`.
- Migra conservando sesión, certificados, adjuntos, configuración y Prosody,
  incluidas opciones adicionales del entorno y preferencias de conversión.
- Impide migraciones simultáneas y permite hasta 60 segundos para el primer
  arranque de Slidge, que puede necesitar migrar su base de datos.
- Crea respaldos con SHA-256 antes del reemplazo; conserva también una candidata
  fallida antes del rollback para permitir recuperar mensajes posteriores.
- Incluye `actualizar-puente-local.ps1` para instalaciones existentes; funciona
  como archivo descargado y directamente mediante `irm URL | iex` sin corromper los acentos ni
  cerrar la consola, y conserva un transcript local para diagnóstico.

Requiere Windows x64 y WSL 2.4.4 o posterior.
