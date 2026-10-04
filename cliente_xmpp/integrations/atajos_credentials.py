from __future__ import annotations

import secrets

SERVICE = "WhatsAppCAN/Atajos"
USERNAME = "Atajos"


def integration_token() -> str:
    from keyring.backends.Windows import WinVaultKeyring

    vault = WinVaultKeyring()
    token = vault.get_password(SERVICE, USERNAME)
    valid = bool(
        token
        and len(token) == 43
        and all(c.isascii() and (c.isalnum() or c in "-_") for c in token)
    )
    if not valid:
        token = secrets.token_urlsafe(32)
        vault.set_password(SERVICE, USERNAME, token)
    return token
