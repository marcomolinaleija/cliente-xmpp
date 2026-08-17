from __future__ import annotations

import argparse
import asyncio
import io
import os
from pathlib import Path
from typing import Any

from aiohttp import ClientSession
from slixmpp import ClientXMPP
from slixmpp.exceptions import IqError

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
PASSWORD_ENV = "WHATSAPP_CAN_LOCAL_SMOKE_PASSWORD"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida slots, subida y descarga XEP-0363 del appliance local."
    )
    parser.add_argument("--jid", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5222)
    parser.add_argument("--ca-file", type=Path, required=True)
    parser.add_argument(
        "--upload-service",
        default="upload.xmpp.whatsappcan.local",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


class UploadSmokeClient(ClientXMPP):
    def __init__(self, args: argparse.Namespace, password: str) -> None:
        super().__init__(args.jid, password)
        self.args = args
        self.completed = asyncio.Event()
        self.failure: BaseException | None = None
        self.uploaded_url = ""
        self.force_starttls = True
        self.enable_starttls = True
        self.enable_direct_tls = False
        self.ca_certs = args.ca_file
        self.register_plugin("xep_0030")
        self.register_plugin("xep_0363")
        self.add_event_handler("session_start", self._run_smoke)

    async def _run_smoke(self, _event: object) -> None:
        try:
            self.send_presence()
            upload = self["xep_0363"]
            info = await upload.find_upload_service(
                domain=self.args.jid.split("@", 1)[-1],
                timeout=10,
            )
            if info is None or str(info["from"]) != self.args.upload_service:
                raise RuntimeError("Prosody no anunció el componente XEP-0363 esperado.")

            advertised_limit = self._advertised_limit(info)
            if advertised_limit != MAX_UPLOAD_BYTES:
                raise RuntimeError(
                    "El límite XEP-0363 anunciado no es exactamente 200 MiB: "
                    f"{advertised_limit} bytes."
                )

            upload.upload_service = info["from"]
            upload.max_file_size = advertised_limit
            payload = b"WhatsApp CAN local upload smoke\n"
            get_url = await upload.upload_file(
                Path("whatsapp-can-smoke.txt"),
                size=len(payload),
                content_type="text/plain",
                input_file=io.BytesIO(payload),
                timeout=10,
            )
            if not get_url.startswith("http://127.0.0.1:5280/file_share/"):
                raise RuntimeError(f"El slot anunció una URL no local: {get_url}")
            self.uploaded_url = get_url

            async with ClientSession() as session:
                async with session.get(get_url, timeout=10) as response:
                    downloaded = await response.read()
                    if response.status != 200 or downloaded != payload:
                        raise RuntimeError(
                            "El archivo subido no pudo recuperarse íntegramente."
                        )

            try:
                await upload.request_slot(
                    info["from"],
                    Path("too-large.bin"),
                    MAX_UPLOAD_BYTES + 1,
                    "application/octet-stream",
                    timeout=10,
                )
            except IqError:
                pass
            else:
                raise RuntimeError("Prosody aceptó un slot mayor de 200 MiB.")
        except BaseException as exc:
            self.failure = exc
        finally:
            self.completed.set()

    @staticmethod
    def _advertised_limit(info: Any) -> int:
        for form in info["disco_info"].iterables:
            values = form["values"]
            if values.get("FORM_TYPE") == ["urn:xmpp:http:upload:0"]:
                limits = values.get("max-file-size", [])
                if limits:
                    if isinstance(limits, str):
                        return int(limits)
                    return int(limits[0])
        raise RuntimeError("El componente XEP-0363 no anunció max-file-size.")


def main() -> int:
    args = parse_args()
    password = os.environ.get(PASSWORD_ENV, "")
    if not password:
        raise SystemExit(f"Falta la contraseña en la variable {PASSWORD_ENV}.")
    if not args.ca_file.is_file():
        raise SystemExit(f"No existe la CA local: {args.ca_file}")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = UploadSmokeClient(args, password)
    try:
        client.connect(args.host, args.port)
        loop.run_until_complete(
            asyncio.wait_for(client.completed.wait(), timeout=args.timeout)
        )
        if client.failure is not None:
            raise client.failure
    finally:
        disconnect = client.disconnect(wait=1, ignore_send_queue=True)
        loop.run_until_complete(disconnect)
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()

    print("Subida XEP-0363 local correcta; límite verificado: 200 MiB.")
    print(f"URL local validada: {client.uploaded_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
