#!/usr/bin/env bash
# Temporary keys and synthetic settings only; no accounts, services or Podman daemon.
set -euo pipefail
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT
export WHATSAPP_CAN_CONFIG_DIR="$test_root/config"
mkdir -p "$WHATSAPP_CAN_CONFIG_DIR/tls"
umask 077
openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 2 \
    -subj '/CN=Fixture Local CA' -addext 'basicConstraints=critical,CA:TRUE' \
    -keyout "$WHATSAPP_CAN_CONFIG_DIR/tls/ca.key" \
    -out "$WHATSAPP_CAN_CONFIG_DIR/tls/ca.crt" >/dev/null 2>&1
helper="$(cd "$(dirname "$0")" && pwd)/rootfs-overlay/usr/local/libexec/whatsapp-can-bridge-voice"
source "$helper"
before_ca="$(sha256sum "$config_dir/tls/ca.crt" "$config_dir/tls/ca.key")"
prepare_tls
before_voice="$(sha256sum "$voice_tls/server.crt" "$voice_tls/server.key")"
prepare_tls
[[ "$(sha256sum "$voice_tls/server.crt" "$voice_tls/server.key")" == "$before_voice" ]]
[[ "$(sha256sum "$config_dir/tls/ca.crt" "$config_dir/tls/ca.key")" == "$before_ca" ]]
openssl verify -CAfile "$config_dir/tls/ca.crt" "$voice_tls/server.crt" >/dev/null
openssl x509 -in "$voice_tls/server.crt" -checkip 127.0.0.1 -noout >/dev/null
[[ "$(stat -c '%u:%g:%a' "$voice_tls")" == '0:10000:750' ]]
[[ "$(stat -c '%u:%g:%a' "$voice_tls/server.key")" == '0:10000:640' ]]
[[ ! -e "$voice_tls/ca.key" ]]
write_environment > "$config_dir/environment"
grep -qx 'CAN_VOICE_ENABLE=1' "$config_dir/environment"
grep -qx 'CAN_VOICE_URL=wss://127.0.0.1:8766/voice/v1' "$config_dir/environment"

mv "$voice_tls/server.key" "$voice_tls/real.key"
ln -s real.key "$voice_tls/server.key"
if prepare_tls; then echo 'Accepted a symlink key' >&2; exit 1; fi
rm -- "$voice_tls/server.key"
mv "$voice_tls/real.key" "$voice_tls/server.key"

# All service/image boundaries below are fixtures.
image_manager="$test_root/image-manager"
printf '#!/bin/sh\nprintf '\''{"image_id":"fixture"}\\n'\''\n' > "$image_manager"
chmod 0700 "$image_manager"
image_ok=false
podman() {
    [[ " $* " == *' --network none '* && " $* " != *' -v '* ]]
    $image_ok
}
systemctl() { printf '%s\n' "$*" >> "$test_root/services"; }
control="$test_root/control"
printf '#!/bin/sh\ntest ! -f "%s/fail-smoke"\n' "$test_root" > "$control"
chmod 0700 "$control"
if enable_voice; then echo 'Unsupported image enabled' >&2; exit 1; fi
grep -qx 'CAN_VOICE_ENABLE=1' "$config_dir/environment"
[[ ! -f "$test_root/services" ]]
image_ok=true
enable_voice
grep -qx 'CAN_VOICE_ENABLE=1' "$config_dir/environment"
curl() {
    [[ " $* " == *" --cacert $config_dir/tls/ca.crt "* ]]
    printf 403
}
smoke_voice
touch "$test_root/fail-smoke"
if enable_voice; then echo 'Failed listener accepted' >&2; exit 1; fi
grep -qx 'CAN_VOICE_ENABLE=1' "$config_dir/environment"
if set_enabled 0; then echo 'Disabled mandatory voice' >&2; exit 1; fi
main required
[[ "$(sort -u "$test_root/services")" == 'restart whatsapp-can-slidge.service' ]]
echo 'PASS required voice: TLS/IP/permissions/idempotence, unsupported image, activation failure stays enabled'
