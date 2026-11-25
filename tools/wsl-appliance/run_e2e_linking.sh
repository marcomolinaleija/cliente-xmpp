#!/bin/sh
# End-to-end linking check on a TEST appliance (never the productive one), as root:
#   wsl -d <test-distro> -u root -- env E2E_TEST_APPLIANCE=1 sh tools/wsl-appliance/run_e2e_linking.sh
# Real Prosody + real bridge_linking.py + real XmppService; WhatsMeow is simulated.
# Leaves the appliance as it found it: the fictitious account is removed and the
# bridge service is started again if it was active.
set -u
# It briefly stops the bridge service; refuse unless the operator marks a test distro.
[ "${E2E_TEST_APPLIANCE:-}" = "1" ] || {
  echo "Set E2E_TEST_APPLIANCE=1 only on a test appliance; never on a linked productive one."
  exit 2
}
IMAGE="${E2E_IMAGE:-ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v14}"
CONTAINER=whatsapp-can-slidge
SERVICE=whatsapp-can-slidge.service
DOMAIN=xmpp.whatsappcan.local
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="e2e-linking-$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
PASSWORD="$(head -c 24 /dev/urandom | base64 | tr -d '/+=\n')"
SECRET="$(sed -n 's/.*component_secret *= *"\(.*\)".*/\1/p' /etc/prosody/prosody.cfg.lua | head -1)"
[ -n "$SECRET" ] || { echo "component secret not found"; exit 2; }
WAS_ACTIVE="$(systemctl is-active "$SERVICE" 2>/dev/null)"
CA_DIR="$(mktemp -d)"
# Only the public certificate; the private key is never mounted.
install -m 0644 /etc/whatsapp-can-bridge/tls/server.crt "$CA_DIR/ca.crt"
chmod 0755 "$CA_DIR"

cleanup() {
  prosodyctl deluser "$NAME@$DOMAIN" >/dev/null 2>&1 || true
  rm -rf "$CA_DIR"
  if [ "$WAS_ACTIVE" = "active" ]; then systemctl start "$SERVICE" >/dev/null 2>&1 || true; sleep 3; fi
  echo "cleanup: account removed; service=$(systemctl is-active "$SERVICE"); container=$(podman ps --format '{{.Names}} {{.Status}}' | grep "$CONTAINER")"
}
trap cleanup EXIT INT TERM

systemctl stop "$SERVICE" >/dev/null 2>&1 || true
podman stop -t 10 "$CONTAINER" >/dev/null 2>&1 || true
sleep 2
printf '%s\n%s\n' "$PASSWORD" "$PASSWORD" | prosodyctl adduser "$NAME@$DOMAIN" >/dev/null 2>&1 \
  || { echo "could not create fictitious account"; exit 2; }
echo "fictitious account created (removed on exit); bridge service was $WAS_ACTIVE"

podman run --rm --network host --entrypoint /venv/bin/python \
  -e E2E_USER="$NAME@$DOMAIN" -e E2E_PASSWORD="$PASSWORD" -e E2E_COMPONENT_SECRET="$SECRET" \
  -e E2E_CA_FILE=/tls/ca.crt -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$REPO":/src:ro \
  -v "$CA_DIR":/tls:ro \
  -w /src "$IMAGE" -B /src/tools/wsl-appliance/e2e_linking.py > "$CA_DIR/out.log" 2>&1
grep -v -i -E 'password|secret' "$CA_DIR/out.log" | grep -E '^(PASS|FAIL|\{)|Traceback|Error' | head -80
grep -q '"failed": \[\]' "$CA_DIR/out.log"
