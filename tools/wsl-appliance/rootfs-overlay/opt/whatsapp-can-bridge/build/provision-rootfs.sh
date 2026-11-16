#!/usr/bin/env bash
# Construye el rootfs genérico del appliance; no genera secretos de usuario.
set -euo pipefail

bridge_image="ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v19"
bridge_digest="sha256:9358df63a39b09d39f6d4f0293b07e1271fd13fed026320057ec6b6de627a899"
# Referencia inicial auditada: ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v19@sha256:9358df63a39b09d39f6d4f0293b07e1271fd13fed026320057ec6b6de627a899
image_directory="/opt/whatsapp-can-bridge/images"
skip_bridge_image=false
reuse_bundled_image=false
image_bundle=false

if [[ "${1:-}" == "--skip-bridge-image" ]]; then
    skip_bridge_image=true
    shift
fi
if [[ "${1:-}" == "--reuse-bundled-image" ]]; then
    reuse_bundled_image=true
    shift
fi
if [[ "${1:-}" == "--image-bundle" ]]; then image_bundle=true; shift; fi
if [[ $# -ne 0 ]]; then
    echo "Uso: provision-rootfs.sh [--skip-bridge-image|--reuse-bundled-image|--image-bundle]" >&2
    exit 64
fi

export DEBIAN_FRONTEND=noninteractive
if ! $image_bundle; then
    echo 'La appliance requiere el bundle con motor de voz.' >&2
    exit 1
fi
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
    /usr/local/libexec/whatsapp-can-bridge-image \
    /usr/local/libexec/whatsapp-can-bridge-voice
chmod 0644 /etc/systemd/system/whatsapp-can-slidge.service

if $image_bundle; then
    metadata="$image_directory/bundled-image.json"
    [[ "$(jq -r '.voice' "$metadata")" == true ]]
    [[ "$(jq -er '.schema_version' "$metadata")" == 1 ]]
    bridge_image="$(jq -er '.image | strings' "$metadata")"
    bridge_digest="$(jq -er '.digest | strings' "$metadata")"
    archive="$(jq -er '.archive | strings' "$metadata")"
    [[ "$bridge_image" =~ ^ghcr\.io/marcomolinaleija/cliente-xmpp-bridge:v[1-9][0-9]*$ ]]
    [[ "$bridge_digest" =~ ^sha256:[0-9a-f]{64}$ ]]
    [[ "$archive" == "slidge-${bridge_image##*:}.oci" ]]
    expected_id="$(jq -er '.image_id | strings' "$metadata")"
    (cd "$image_directory" && sha256sum -c "$archive.sha256")
    podman load -i "$image_directory/$archive"
    imported_id="$(podman image inspect "$bridge_image" --format '{{.Id}}')"
    [[ "${imported_id#sha256:}" == "${expected_id#sha256:}" ]]
    podman run --rm --pull=never --network none --env CAN_VOICE_ENABLE=0 \
        --entrypoint python "$imported_id" -B /opt/voice-tools/smoke_voice_integration.py
    printf '%s\n' "$imported_id" > "$image_directory/${archive%.oci}.image-id"
    jq --arg image "$bridge_image" --arg digest "$bridge_digest" \
        --argjson voice "$(jq -r '.voice // false' "$metadata")" \
        '.bridge_image=$image | .bridge_digest=$digest | .bridge_voice=$voice' \
        /opt/whatsapp-can-bridge/version.json > /opt/whatsapp-can-bridge/version.json.tmp
    mv /opt/whatsapp-can-bridge/version.json.tmp /opt/whatsapp-can-bridge/version.json
    podman image rm "$bridge_image"
    if [[ "$archive" != slidge-v19.oci ]]; then
        rm -f "$image_directory/slidge-v19.oci" "$image_directory/slidge-v19.oci.sha256" "$image_directory/slidge-v19.image-id"
    fi
elif ! $skip_bridge_image; then
    rm -f "$image_directory/bundled-image.json"
    if $reuse_bundled_image; then
        # The Windows builder accepts this path only from the digest-verified
        # generic package with the same bundled bridge. Still verify OCI bytes
        # and its actual import ID before exporting the new rootfs.
        (cd "$image_directory" && sha256sum -c slidge-v19.oci.sha256)
        approved_id="$(tr -d '\r\n' < "$image_directory/slidge-v19.image-id")"
    else
    # Se guarda la imagen OCI y su ID para que la instalación final no dependa de la red.
    podman pull "$bridge_image@$bridge_digest"
    podman tag "$bridge_image@$bridge_digest" "$bridge_image"
    podman save --format oci-archive -o "$image_directory/slidge-v19.oci" "$bridge_image"
    sha256sum "$image_directory/slidge-v19.oci" > "$image_directory/slidge-v19.oci.sha256"
    podman image rm "$bridge_image" "$bridge_image@$bridge_digest" || true
    fi
    # Podman puede normalizar la configuración al cargar un OCI archive. El ID aprobado debe ser
    # el del ciclo real que ejecutará la instalación, no el de la imagen antes de guardarla.
    podman load -i "$image_directory/slidge-v19.oci"
    actual_id="$(podman image inspect "$bridge_image" --format '{{.Id}}')"
    if $reuse_bundled_image && [[ "${actual_id#sha256:}" != "${approved_id#sha256:}" ]]; then
        echo 'El ID de la imagen incluida cambió inesperadamente al importarla.' >&2
        exit 1
    fi
    printf '%s\n' "$actual_id" > "$image_directory/slidge-v19.image-id"
    podman image rm "$bridge_image" || true
fi

# El rootfs debe conservar machine-id como archivo vacío. Si falta, systemd-firstboot intenta
# abrir un asistente interactivo en una instalación WSL sin consola.
: > /etc/machine-id
rm -f /var/lib/dbus/machine-id
ln -s /etc/machine-id /var/lib/dbus/machine-id

systemctl enable prosody.service nginx.service whatsapp-can-slidge.service
systemctl stop whatsapp-can-slidge.service nginx.service prosody.service
rm -f /var/lib/prosody/prosody.sock
