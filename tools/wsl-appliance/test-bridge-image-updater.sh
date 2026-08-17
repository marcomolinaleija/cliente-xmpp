#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
export WHATSAPP_CAN_STATE_DIR="$test_root/state"
export WHATSAPP_CAN_CONFIG_DIR="$test_root/config"
export WHATSAPP_CAN_SKIP_ROOT_CHECK=true
mkdir -p "$WHATSAPP_CAN_STATE_DIR/slidge" "$WHATSAPP_CAN_CONFIG_DIR"
printf 'original-state\n' > "$WHATSAPP_CAN_STATE_DIR/slidge/session.db"

updater="$(cd "$(dirname "$0")" && pwd)/rootfs-overlay/usr/local/libexec/whatsapp-can-bridge-image"
# shellcheck source=/dev/null
source "$updater"

raw_id="$(printf 'a%.0s' {1..64})"
[[ "$(normalize_image_id "$raw_id")" == "sha256:$raw_id" ]]
[[ "$(normalize_image_id "sha256:$raw_id")" == "sha256:$raw_id" ]]
if normalize_image_id "not-an-image-id" >/dev/null 2>&1; then
    echo "Se aceptó inesperadamente un ID local inválido." >&2
    exit 1
fi

old_image="ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v19"
old_digest="sha256:9358df63a39b09d39f6d4f0293b07e1271fd13fed026320057ec6b6de627a899"
old_id="sha256:$(printf '1%.0s' {1..64})"
new_image="ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v20"
new_digest="sha256:$(printf '2%.0s' {1..64})"
new_id="sha256:$(printf '3%.0s' {1..64})"
write_active_image "$old_image" "$old_digest" "$old_id"

manifest="$test_root/manifest.json"
jq -n --arg image "$new_image" --arg digest "$new_digest" \
    '{schema_version:1,image:$image,digest:$digest}' > "$manifest"

events=()
image_exists() { return 0; }
pull_image() { events+=("pull:$1"); }
inspect_image_id() { printf '%s\n' "$new_id"; }
stop_bridge() { events+=("stop"); }
start_bridge() { events+=("start"); }
capture_failed_update_logs() { : > "$1"; }
validate_candidate() { events+=("validate"); return 0; }

update_from_manifest "$manifest"
load_active_image
[[ "$active_image" == "$new_image" ]]
[[ "$active_digest" == "$new_digest" ]]
[[ "$active_image_id" == "$new_id" ]]
[[ "$(cat "$WHATSAPP_CAN_STATE_DIR/slidge/session.db")" == "original-state" ]]
[[ " ${events[*]} " == *" pull:$new_image@$new_digest stop start validate "* ]]
backup_count="$(find "$WHATSAPP_CAN_STATE_DIR/update-backups" -mindepth 1 -maxdepth 1 -type d | wc -l)"
[[ "$backup_count" -eq 1 ]]

printf 'candidate-state\n' > "$WHATSAPP_CAN_STATE_DIR/slidge/session.db"
write_active_image "$old_image" "$old_digest" "$old_id"
events=()
validate_candidate() {
    events+=("validate")
    if [[ "$active_validation_failed" == "false" ]]; then
        active_validation_failed=true
        return 1
    fi
    return 0
}
active_validation_failed=false
sleep 1
if update_from_manifest "$manifest"; then
    echo "La actualización defectuosa debió devolver error." >&2
    exit 1
fi
load_active_image
[[ "$active_image" == "$old_image" ]]
[[ "$active_digest" == "$old_digest" ]]
[[ "$active_image_id" == "$old_id" ]]
[[ "$(cat "$WHATSAPP_CAN_STATE_DIR/slidge/session.db")" == "candidate-state" ]]
[[ -d "$(find "$WHATSAPP_CAN_STATE_DIR/update-backups" -mindepth 1 -maxdepth 1 -type d | sort | tail -n1)/failed-slidge" ]]

invalid_manifest="$test_root/invalid.json"
jq -n --arg image "ghcr.io/example/foreign:v20" --arg digest "$new_digest" \
    '{schema_version:1,image:$image,digest:$digest}' > "$invalid_manifest"
if read_update_manifest "$invalid_manifest" >/dev/null 2>&1; then
    echo "Se aceptó inesperadamente un repositorio no autorizado." >&2
    exit 1
fi

downgrade_manifest="$test_root/downgrade.json"
jq -n \
    --arg image "ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v18" \
    --arg digest "sha256:$(printf '4%.0s' {1..64})" \
    '{schema_version:1,image:$image,digest:$digest}' > "$downgrade_manifest"
if update_from_manifest "$downgrade_manifest" >/dev/null 2>&1; then
    echo "Se aceptó inesperadamente un downgrade del canal estable." >&2
    exit 1
fi

retagged_manifest="$test_root/retagged.json"
jq -n \
    --arg image "$old_image" \
    --arg digest "sha256:$(printf '5%.0s' {1..64})" \
    '{schema_version:1,image:$image,digest:$digest}' > "$retagged_manifest"
if update_from_manifest "$retagged_manifest" >/dev/null 2>&1; then
    echo "Se aceptó inesperadamente otro digest para la versión instalada." >&2
    exit 1
fi

echo "Bridge image updater tests: ok"
