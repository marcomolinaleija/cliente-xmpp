#!/usr/bin/env bash
# Run manually with sudo on the configured XMPP server after reviewing both scripts.
set -euo pipefail
umask 077
if [[ ${EUID} != 0 ]]; then
    echo "Ejecuta con sudo bash /ruta/privada/repair-certificates.sh" >&2
    exit 1
fi
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_hook=$source_dir/deploy-certificates.sh
hook=/etc/letsencrypt/renewal-hooks/deploy/50-xmpp-certificates.sh
[[ -f "$source_hook" && ! -L "$source_hook" ]]
bash -n "$source_hook"
nginx -t
docker exec prosody-xmpp prosodyctl check config
[[ -f /etc/letsencrypt/renewal/xmpp.example.test.conf ]]
backup=$(mktemp -d /root/xmpp-renewal-backup.XXXXXXXX)
cp -p /etc/letsencrypt/renewal/xmpp.example.test.conf "$backup/"
cp -Lp /etc/nginx/sites-enabled/xmpp.example.test-invite-portal.conf "$backup/"
if [[ -f "$hook" ]]; then cp -p "$hook" "$backup/deploy-hook.before"; fi
install -o root -g root -m 0755 "$source_hook" "$hook"
echo "Preparado el despliegue automático de certificados XMPP. Respaldo: $backup"
# Preserve the existing certificate's domains and renew only this lineage.
# Certbot skips issuance if its existing certificate is already current.
certbot renew --cert-name xmpp.example.test --non-interactive
# Also synchronize when Certbot skipped renewal (fresh lineage but stale copies).
RENEWED_LINEAGE=/etc/letsencrypt/live/xmpp.example.test "$hook"
openssl x509 -in /opt/xmpp/certs/fullchain.pem -noout -dates -ext subjectAltName
echo "Certificados XMPP corregidos y verificados. Ya puedes reconectar el cliente."
