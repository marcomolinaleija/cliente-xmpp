"""Verify local voice TLS and anonymous rejection; no token, account or microphone."""

from __future__ import annotations

import argparse
import ssl
import urllib.error
import urllib.request
from pathlib import Path


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("El servicio local de voz no debe redirigir solicitudes.")


def verify(ca_file: Path) -> None:
    tls = ssl.create_default_context(cafile=str(ca_file))
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=tls),
        RejectRedirects(),
    )
    for headers, expected in (({}, 400), ({"Sec-WebSocket-Protocol": "can-voice-1"}, 403)):
        request = urllib.request.Request("https://127.0.0.1:8766/voice/v1", headers=headers)
        try:
            with opener.open(request, timeout=5):
                raise ValueError("El servicio de voz aceptó una solicitud sin autorización.")
        except urllib.error.HTTPError as error:
            with error:
                if error.code != expected:
                    raise ValueError(f"Respuesta inesperada de voz: HTTP {error.code}.") from error
    print("Voz local: TLS verificado desde Windows y acceso anónimo rechazado (400/403).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ca-file", type=Path, required=True)
    args = parser.parse_args()
    verify(args.ca_file)


if __name__ == "__main__":
    main()
