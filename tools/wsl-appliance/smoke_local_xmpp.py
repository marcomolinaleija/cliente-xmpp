from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path
from typing import Any

from cliente_xmpp.config.credentials import CredentialStore
from cliente_xmpp.config.settings import ConnectionSettings
from cliente_xmpp.xmpp.client import XmppService
from cliente_xmpp.xmpp.events import WhatsAppBridgeStatus, XmppConnected, XmppError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida la autenticación del cliente contra el appliance WSL local."
    )
    parser.add_argument("connection_json", type=Path)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--wait-whatsapp-status",
        action="store_true",
        help="Espera además el estado de vinculación anunciado por Slidge.",
    )
    return parser.parse_args()


def load_connection(path: Path) -> tuple[ConnectionSettings, str]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    jid = str(data["jid"])
    password = str(data.get("password", "")) or CredentialStore().get_password(jid)
    if not password:
        raise ValueError("No existe una contraseña local en el contrato ni en Windows.")
    settings = ConnectionSettings(
        jid=jid,
        host=str(data.get("host", "127.0.0.1")),
        port=int(data.get("port", 5222)),
        use_tls=bool(data.get("use_tls", False)),
        ca_file=str(data.get("ca_file", "")),
    )
    return settings, password


def main() -> int:
    args = parse_args()
    settings, password = load_connection(args.connection_json.resolve())
    completed = threading.Event()
    outcome: dict[str, bool | str] = {
        "connected": False,
        "error": False,
        "whatsapp_status": "",
    }
    service: XmppService

    def emit(event: object) -> None:
        if isinstance(event, XmppConnected):
            outcome["connected"] = True
            if not args.wait_whatsapp_status:
                completed.set()
        elif isinstance(event, XmppError):
            outcome["error"] = True
            completed.set()
        elif isinstance(event, WhatsAppBridgeStatus):
            outcome["whatsapp_status"] = event.status
            completed.set()

    service = XmppService(emit)
    service.connect(settings, password)
    finished = completed.wait(args.timeout)
    service.disconnect()

    if not finished:
        print("FALLO: la autenticación XMPP local agotó el tiempo de espera.")
        return 1
    if outcome["error"] or not outcome["connected"]:
        print("FALLO: el cliente rechazó la conexión XMPP local.")
        return 1

    if args.wait_whatsapp_status and not outcome["whatsapp_status"]:
        print("FALLO: Slidge no anunció el estado de vinculación de WhatsApp.")
        return 1

    print("Autenticación XMPP local correcta.")
    if outcome["whatsapp_status"]:
        print(f"Estado de WhatsApp: {outcome['whatsapp_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
