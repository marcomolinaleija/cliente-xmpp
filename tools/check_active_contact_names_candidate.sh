#!/bin/sh
set -eu

binary=/venv/lib/python3.13/site-packages/slidge_whatsapp/generated/_whatsapp.cpython-313-x86_64-linux-gnu.so
printf 'patched binary markers: '
docker exec slidge-whatsapp grep -a -c \
    'Could not load stored contact info' "$binary"
printf 'roster sync completions: '
docker logs --since 15m slidge-whatsapp 2>&1 \
    | grep -Fc 'Automatic XMPP roster sync completed' || true
printf 'roster sync failures: '
docker logs --since 15m slidge-whatsapp 2>&1 \
    | grep -Fc 'Automatic XMPP roster sync failed' || true
printf 'stored contact warnings: '
docker logs --since 15m slidge-whatsapp 2>&1 \
    | grep -Fc 'Could not load stored contact info' || true
