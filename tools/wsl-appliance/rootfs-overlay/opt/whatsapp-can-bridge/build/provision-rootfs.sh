#!/usr/bin/env bash
# Construye el rootfs genérico del appliance; no genera secretos de usuario.
set -euo pipefail

bridge_image="ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v19"
bridge_digest="sha256:9358df63a39b09d39f6d4f0293b07e1271fd13fed026320057ec6b6de627a899"
# Referencia inicial auditada: ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v19@sha256:9358df63a39b09d39f6d4f0293b07e1271fd13fed026320057ec6b6de627a899
image_directory="/opt/whatsapp-can-bridge/images"
skip_bridge_image=false

if [[ "${1:-}" == "--skip-bridge-image" ]]; then
    skip_bridge_image=true
    shift
fi
if [[ $# -ne 0 ]]; then
    echo "Uso: provision-rootfs.sh [--skip-bridge-image]" >&2
    exit 64
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    jq \
    nginx \
    openssl \
    podman \
    prosody \
    prosody-modules \
    tini
apt-get clean
rm -rf /var/lib/apt/lists/*

install -d -m 0755 "$image_directory"
install -d -m 0755 /var/lib/whatsapp-can-bridge
install -d -m 0750 /etc/whatsapp-can-bridge
chmod 0755 \
    /usr/local/sbin/whatsapp-can-bridge \
    /usr/local/libexec/whatsapp-can-bridge-image
chmod 0644 /etc/systemd/system/whatsapp-can-slidge.service

if ! $skip_bridge_image; then
    # Se guarda la imagen OCI y su ID para que la instalación final no dependa de la red.
    podman pull "$bridge_image@$bridge_digest"
    podman tag "$bridge_image@$bridge_digest" "$bridge_image"
    podman save --format oci-archive -o "$image_directory/slidge-v19.oci" "$bridge_image"
    sha256sum "$image_directory/slidge-v19.oci" > "$image_directory/slidge-v19.oci.sha256"
    podman image rm "$bridge_image" "$bridge_image@$bridge_digest" || true
    # Podman puede normalizar la configuración al cargar un OCI archive. El ID aprobado debe ser
    # el del ciclo real que ejecutará la instalación, no el de la imagen antes de guardarla.
    podman load -i "$image_directory/slidge-v19.oci"
    podman image inspect "$bridge_image" --format '{{.Id}}' > "$image_directory/slidge-v19.image-id"
    podman image rm "$bridge_image" || true
fi

# El rootfs debe conservar machine-id como archivo vacío. Si falta, systemd-firstboot intenta
# abrir un asistente interactivo en una instalación WSL sin consola.
: > /etc/machine-id
rm -f /var/lib/dbus/machine-id
ln -s /etc/machine-id /var/lib/dbus/machine-id

systemctl enable prosody.service nginx.service whatsapp-can-slidge.service
