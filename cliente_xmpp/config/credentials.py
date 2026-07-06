from __future__ import annotations

SERVICE_NAME = "cliente-xmpp"


class CredentialStore:
    def get_password(self, jid: str) -> str:
        if not jid:
            return ""

        keyring = _load_keyring()
        if keyring is None:
            return ""

        try:
            return keyring.get_password(SERVICE_NAME, jid) or ""
        except Exception:
            return ""

    def save_password(self, jid: str, password: str) -> None:
        if not jid or not password:
            return

        keyring = _load_keyring()
        if keyring is None:
            return

        try:
            keyring.set_password(SERVICE_NAME, jid, password)
        except Exception:
            return

    def delete_password(self, jid: str) -> None:
        if not jid:
            return

        keyring = _load_keyring()
        if keyring is None:
            return

        try:
            keyring.delete_password(SERVICE_NAME, jid)
        except Exception:
            return


def _load_keyring() -> object | None:
    try:
        import keyring
    except ImportError:
        return None

    return keyring
