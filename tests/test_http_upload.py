from __future__ import annotations

import asyncio
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.client_exceptions import ClientConnectorError

from cliente_xmpp.xmpp.client import BridgeXmppClient, XmppService
from cliente_xmpp.xmpp.events import XmppError
from cliente_xmpp.xmpp.http_upload import (
    is_dns_resolution_error,
    upload_file_with_system_resolver,
)


def _dns_connector_error() -> ClientConnectorError:
    connection_key = SimpleNamespace(host="xmpp.example.test", port=5281, ssl=True)
    return ClientConnectorError(
        connection_key,
        OSError(None, "Could not contact DNS servers"),
    )


class _Response:
    status = 201

    def __init__(self) -> None:
        self.close = Mock()

    async def text(self) -> str:
        return ""


class _Session:
    def __init__(self) -> None:
        self.response = _Response()
        self.uploaded = b""
        self.put_url = ""
        self.put_headers: dict[str, str] = {}

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def put(
        self,
        url: str,
        *,
        data: object,
        headers: dict[str, str],
        timeout: int,
    ) -> _Response:
        self.put_url = url
        self.put_headers = headers
        self.uploaded = data.read()
        self.timeout = timeout
        return self.response


class _BridgeUploadHarness:
    _upload_file_with_default_resolver = staticmethod(
        BridgeXmppClient._upload_file_with_default_resolver
    )

    def __init__(self, upload: object) -> None:
        self.upload = upload

    def __getitem__(self, _key: str) -> object:
        return self.upload


class HttpUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_dns_error_is_recognized_from_aiohttp_connector_error(self) -> None:
        self.assertTrue(is_dns_resolution_error(_dns_connector_error()))
        self.assertTrue(
            is_dns_resolution_error(
                ClientConnectorError(
                    SimpleNamespace(host="xmpp.example.test", port=5281, ssl=True),
                    socket.gaierror(11001, "getaddrinfo failed"),
                )
            )
        )
        self.assertFalse(
            is_dns_resolution_error(
                ClientConnectorError(
                    SimpleNamespace(host="xmpp.example.test", port=5281, ssl=True),
                    ConnectionRefusedError(10061, "connection refused"),
                )
            )
        )

    async def test_system_resolver_upload_requests_fresh_slot_and_streams_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_note = Path(temp_dir) / "ptt-test.ogg"
            voice_note.write_bytes(b"voice note")
            upload = SimpleNamespace(
                upload_service="upload.example.test",
                max_file_size=1024,
                default_content_type="application/octet-stream",
                request_slot=AsyncMock(
                    return_value={
                        "http_upload_slot": {
                            "put": {
                                "url": "https://xmpp.example.test:5281/upload/token",
                                "headers": [{"name": "Authorization", "value": "secret"}],
                            },
                            "get": {"url": "https://xmpp.example.test:5281/file/token"},
                        }
                    }
                ),
            )
            session = _Session()
            resolver = object()
            connector = object()

            with (
                patch(
                    "cliente_xmpp.xmpp.http_upload.ThreadedResolver",
                    return_value=resolver,
                ) as resolver_factory,
                patch(
                    "cliente_xmpp.xmpp.http_upload.TCPConnector",
                    return_value=connector,
                ) as connector_factory,
                patch(
                    "cliente_xmpp.xmpp.http_upload.ClientSession",
                    return_value=session,
                ) as session_factory,
            ):
                get_url = await upload_file_with_system_resolver(
                    upload,
                    voice_note,
                    size=voice_note.stat().st_size,
                    content_type="audio/ogg",
                    timeout=60,
                )

            self.assertEqual(get_url, "https://xmpp.example.test:5281/file/token")
            self.assertEqual(session.uploaded, b"voice note")
            self.assertEqual(session.put_headers["Authorization"], "secret")
            self.assertEqual(session.put_headers["Content-Type"], "audio/ogg")
            resolver_factory.assert_called_once_with()
            connector_factory.assert_called_once_with(resolver=resolver)
            self.assertEqual(session_factory.call_args.kwargs["connector"], connector)
            session.response.close.assert_called_once_with()

    async def test_bridge_retries_dns_failure_with_system_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_note = Path(temp_dir) / "ptt-test.ogg"
            voice_note.write_bytes(b"voice note")
            upload = SimpleNamespace(
                upload_service="upload.example.test",
                upload_file=AsyncMock(side_effect=_dns_connector_error()),
            )
            client = _BridgeUploadHarness(upload)

            with (
                patch(
                    "cliente_xmpp.xmpp.client.upload_file_with_system_resolver",
                    new=AsyncMock(return_value="https://xmpp.example.test/file/token"),
                ) as fallback,
                patch("cliente_xmpp.xmpp.client.asyncio.sleep", new=AsyncMock()) as sleep,
            ):
                get_url = await BridgeXmppClient._upload_file(
                    client,
                    voice_note,
                    size=voice_note.stat().st_size,
                    content_type="audio/ogg",
                    timeout=60,
                )

            self.assertEqual(get_url, "https://xmpp.example.test/file/token")
            fallback.assert_awaited_once()
            sleep.assert_awaited_once_with(0.5)

    async def test_service_deletes_temporary_voice_note_after_final_send_error(self) -> None:
        emitted: list[object] = []
        service = XmppService(emitted.append)
        service._client = SimpleNamespace(
            send_file=AsyncMock(side_effect=RuntimeError("upload failed"))
        )
        service._loop = asyncio.get_running_loop()

        with patch("cliente_xmpp.xmpp.client.delete_temporary_voice_note") as delete:
            service.send_file("contact@example.test", "ptt-test.ogg")
            await self._wait_for_emit(emitted)

        delete.assert_called_once_with("ptt-test.ogg")
        self.assertIsInstance(emitted[0], XmppError)

    @staticmethod
    async def _wait_for_emit(emitted: list[object]) -> None:
        for _attempt in range(10):
            if emitted:
                return
            await asyncio.sleep(0)
        raise AssertionError("The scheduled upload did not finish")


if __name__ == "__main__":
    unittest.main()
