#!/usr/bin/env bash
set -euo pipefail
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT
source "$(cd "$(dirname "$0")" && pwd)/rootfs-overlay/usr/local/sbin/whatsapp-can-bridge"
generated="$test_root/generated"
previous="$test_root/previous"
cat > "$generated" <<'EOF'
SLIDGE_JID=whatsapp.fixture.invalid
SLIDGE_SECRET=new-synthetic-secret
SLIDGE_CONVERT_STICKERS=true
CAN_VOICE_ENABLE=1
CAN_VOICE_URL=wss://127.0.0.1:8766/voice/v1
EOF
cat > "$previous" <<'EOF'
SLIDGE_SECRET=old-synthetic-secret
SLIDGE_CONVERT_STICKERS=false
CAN_VOICE_ENABLE=0
CAN_VOICE_URL=wss://obsolete.fixture.invalid/voice/v1
CAN_VOICE_SOCKET=/old/native.sock
DEEPGRAM_API_KEY=synthetic-key==
CAN_VOICE_METRICS=1
LITERAL=$(touch "$test_root/should-not-exist")
EOF
merge_environment "$generated" "$previous" > "$test_root/merged"
grep -qx 'SLIDGE_SECRET=new-synthetic-secret' "$test_root/merged"
grep -qx 'SLIDGE_CONVERT_STICKERS=false' "$test_root/merged"
grep -qx 'CAN_VOICE_ENABLE=1' "$test_root/merged"
grep -qx 'CAN_VOICE_URL=wss://127.0.0.1:8766/voice/v1' "$test_root/merged"
grep -qx 'DEEPGRAM_API_KEY=synthetic-key==' "$test_root/merged"
grep -qx 'CAN_VOICE_METRICS=1' "$test_root/merged"
grep -Fqx 'LITERAL=$(touch "$test_root/should-not-exist")' "$test_root/merged"
[[ ! -e "$test_root/should-not-exist" ]]
[[ "$(grep -c '^SLIDGE_CONVERT_STICKERS=' "$test_root/merged")" == 1 ]]
! grep -q '^CAN_VOICE_SOCKET=' "$test_root/merged"
merge_environment "$generated" "$test_root/absent" > "$test_root/fresh"
cmp "$generated" "$test_root/fresh"
ln -s "$previous" "$test_root/link"
if merge_environment "$generated" "$test_root/link" > /dev/null; then
    echo 'Accepted a symlink environment' >&2; exit 1
fi
echo 'PASS environment migration: retained settings/secrets, managed fields, no evaluation, no links'
