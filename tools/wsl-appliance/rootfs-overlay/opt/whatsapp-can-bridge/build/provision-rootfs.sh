#!/usr/bin/env bash
# Construye el rootfs genérico del appliance; no genera secretos de usuario.
set -euo pipefail

bridge_image="ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v14"
bridge_digest="sha256:3efeae0eb471bf131fc6af388569ecbd052c14012f6fb963e043a2d1b0760f8f"
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

if ! $skip_bridge_image; then
    # Se guarda la imagen OCI y su ID para que la instalación final no dependa de la red.
    podman pull "$bridge_image@$bridge_digest"
    podman tag "$bridge_image@$bridge_digest" "$bridge_image"
    podman save --format oci-archive -o "$image_directory/slidge-v14.oci" "$bridge_image"
    sha256sum "$image_directory/slidge-v14.oci" > "$image_directory/slidge-v14.oci.sha256"
    podman image inspect "$bridge_image" --format '{{.Id}}' > "$image_directory/slidge-v14.image-id"
    podman image rm "$bridge_image" "$bridge_image@$bridge_digest" || true
fi

# El rootfs debe conservar machine-id como archivo vacío. Si falta, systemd-firstboot intenta
# abrir un asistente interactivo en una instalación WSL sin consola.
: > /etc/machine-id
rm -f /var/lib/dbus/machine-id
ln -s /etc/machine-id /var/lib/dbus/machine-id

systemctl enable prosody.service nginx.service whatsapp-can-slidge.service
