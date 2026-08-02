#!/bin/sh
set -eu

candidate="${1:-cliente-xmpp-bridge:contact-names-candidate}"
base="ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v10"

docker image inspect "$candidate" --format 'candidate {{.Id}} {{.Size}}'
docker image inspect "$base" --format 'base {{.Id}} {{.Size}}'

docker run --rm --entrypoint slidge-whatsapp "$candidate" --help \
    >/tmp/contact-candidate-help.txt
sed -n '1,8p' /tmp/contact-candidate-help.txt

docker run --rm --entrypoint python "$candidate" -c \
    'import slidge_whatsapp; import slidge_whatsapp.generated._whatsapp; print("native import: ok")'

container_id="$(docker create "$candidate")"
trap 'docker rm -f "$container_id" >/dev/null 2>&1 || true' EXIT
docker cp \
    "$container_id:/venv/lib/python3.13/site-packages/slidge_whatsapp/generated/_whatsapp.cpython-313-x86_64-linux-gnu.so" \
    /tmp/contact-candidate-whatsapp.so
docker rm "$container_id" >/dev/null
trap - EXIT

docker run --rm -v /tmp/contact-candidate-whatsapp.so:/tmp/whatsapp.so:ro \
    golang:1.25-trixie sh -c \
    "readelf -Ws /tmp/whatsapp.so | awk '\$5 == \"GLOBAL\" && \$7 != \"UND\" {print \$8}' | sort -u" \
    >/tmp/contact-candidate-symbols.txt
grep -Fx 'PyInit__whatsapp' /tmp/contact-candidate-symbols.txt >/dev/null
symbol_count="$(wc -l </tmp/contact-candidate-symbols.txt)"
printf 'native exported symbols: %s\n' "$symbol_count"
