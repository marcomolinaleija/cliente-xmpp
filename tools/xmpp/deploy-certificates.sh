#!/usr/bin/env bash
# Deployment hook for the inspected the configured XMPP server layout. Never prints a private key.
set -euo pipefail
umask 077

domain=xmpp.example.test
lineage=/etc/letsencrypt/live/$domain
destination=/opt/xmpp/certs
container=prosody-xmpp

if [[ ${EUID} != 0 ]]; then
    echo "Ejecuta este script con sudo." >&2
    exit 1
fi
# Certbot runs directory hooks for other certificates too.
if [[ -n ${RENEWED_LINEAGE:-} && ${RENEWED_LINEAGE%/} != "$lineage" ]]; then
    exit 0
fi
[[ -f "$lineage/cert.pem" && -f "$lineage/chain.pem" && -f "$lineage/fullchain.pem" && -f "$lineage/privkey.pem" ]]
[[ -d "$destination" && ! -L "$destination" ]]
for name in "$domain" "whatsapp.$domain"; do
    openssl verify -verify_hostname "$name" -CAfile /etc/ssl/certs/ca-certificates.crt \
        -untrusted "$lineage/chain.pem" "$lineage/cert.pem"
done
cert_key=$(openssl x509 -in "$lineage/cert.pem" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum)
private_key=$(openssl pkey -in "$lineage/privkey.pem" -pubout -outform DER | sha256sum)
[[ "$cert_key" == "$private_key" ]] || { echo "La clave no corresponde al certificado." >&2; exit 1; }

# Check certificates actually served, including STARTTLS, without authentication.
verify_served() {
    python3 - "$domain" "$lineage/cert.pem" <<'PY'
import hashlib
import socket
import ssl
import sys

domain, path = sys.argv[1:]
with open(path, encoding="ascii") as source:
    expected = hashlib.sha256(ssl.PEM_cert_to_DER_cert(source.read())).digest()
context = ssl.create_default_context()
def receive(sock, marker):
    data = b""
    while marker not in data:
        part = sock.recv(4096)
        if not part or len(data) > 65536:
            raise RuntimeError("Respuesta XMPP inesperada")
        data += part
    return data

try:
    for port in (5222, 5281, 443):
        with socket.create_connection(("127.0.0.1", port), timeout=8) as raw:
            if port == 5222:
                raw.sendall((f"<stream:stream to='{domain}' xmlns='jabber:client' "
                             "xmlns:stream='http://etherx.jabber.org/streams' version='1.0'>").encode())
                receive(raw, b"</stream:features>")
                raw.sendall(b"<starttls xmlns='urn:ietf:params:xml:ns:xmpp-tls'/>")
                reply = receive(raw, b">")
                if b"proceed" not in reply:
                    raise RuntimeError("STARTTLS rechazado")
            with context.wrap_socket(raw, server_hostname=domain) as connection:
                if hashlib.sha256(connection.getpeercert(binary_form=True)).digest() != expected:
                    raise RuntimeError("El servicio presenta otro certificado")
except Exception as error:
    print(f"Verificación pendiente: {type(error).__name__}", file=sys.stderr)
    sys.exit(1)
print("TLS válido y certificado actualizado en 5222, 5281 y 443.")
PY
}

if cmp -s "$lineage/fullchain.pem" "$destination/fullchain.pem" \
    && cmp -s "$lineage/privkey.pem" "$destination/privkey.pem" \
    && verify_served; then
    exit 0
fi
nginx -t
docker exec "$container" prosodyctl check config
group_id=$(docker exec "$container" id -g prosody)
[[ "$group_id" =~ ^[0-9]+$ ]]
backup=$(mktemp -d /root/xmpp-cert-backup.XXXXXXXX)
cp -p "$destination/fullchain.pem" "$destination/privkey.pem" "$backup/"
echo "Respaldo privado guardado en $backup"

# Stage both files; service reload happens only after both are installed.
install -o root -g root -m 0644 "$lineage/fullchain.pem" "$destination/fullchain.pem.new"
install -o root -g "$group_id" -m 0640 "$lineage/privkey.pem" "$destination/privkey.pem.new"
mv -f "$destination/fullchain.pem.new" "$destination/fullchain.pem"
mv -f "$destination/privkey.pem.new" "$destination/privkey.pem"
nginx -t
systemctl reload nginx
docker exec "$container" prosodyctl reload
# Some Prosody versions retain an old HTTPS context after a reload.
sleep 2
if ! verify_served; then
    echo "Recargando el contenedor Prosody para actualizar todos sus contextos TLS."
    docker restart --time 15 "$container" >/dev/null
    for attempt in 1 2 3 4 5; do
        sleep 2
        if verify_served; then exit 0; fi
    done
    echo "No se confirmó TLS en todos los puertos. Revisa el servicio; no se repitió la renovación." >&2
    exit 1
fi
