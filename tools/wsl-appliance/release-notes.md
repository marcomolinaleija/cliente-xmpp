# Appliance local de WhatsApp CAN 1.0.0

Primera distribución pública del puente local para WSL2.

- Incluye Ubuntu 24.04, Prosody, nginx, Podman y el puente de WhatsApp v14.
- Publica XMPP y adjuntos únicamente en `127.0.0.1`.
- Genera credenciales, CA y certificado distintos para cada instalación.
- Conserva la sesión de WhatsApp y los adjuntos dentro de la distribución WSL2.
- Permite instalación, reanudación, diagnóstico, respaldo y desinstalación segura.

Requiere Windows x64 y WSL 2.4.4 o posterior.
