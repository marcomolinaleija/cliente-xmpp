#!/bin/sh
set -eu

candidate="${1:-cliente-xmpp-bridge:contact-names-candidate3}"
database="${2:-/opt/xmpp/slidge/contact-name-test-backups/20260719T165245Z/whatsapp.db}"
source_dir="$(mktemp -d /tmp/contact-store-test.XXXXXX)"
container_id="$(docker create "$candidate")"
cleanup() {
    docker rm -f "$container_id" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker cp \
    "$container_id:/venv/lib/python3.13/site-packages/slidge_whatsapp/." \
    "$source_dir/"
docker rm "$container_id" >/dev/null
trap - EXIT
cp /tmp/bridge_contact_name_store_test.go \
    "$source_dir/bridge_contact_name_store_test.go"

if docker run --rm \
        -v "$source_dir:/src" \
        -v "$database:/data/whatsapp.db:ro" \
        -v contact-name-go-cache:/root/.cache/go-build \
        -w /src \
        -e WHATSAPP_TEST_DATABASE=/data/whatsapp.db \
        golang:1.25-trixie sh -c \
        '/usr/local/go/bin/gofmt -w bridge_contact_name_store_test.go && /usr/local/go/bin/go test -run TestStoredContactInfoAgainstDatabase -v . >contact_store_test_output.txt 2>&1'; then
    cat "$source_dir/contact_store_test_output.txt"
else
    cat "$source_dir/contact_store_test_output.txt"
    exit 1
fi
