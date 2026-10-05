from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from xml.etree import ElementTree as ET

from slixmpp import Iq, Message

from cliente_xmpp.xmpp.client import BridgeXmppClient
from tools.smoke_bridge_native_sticker_pack_runtime import fixture


class PackClient(SimpleNamespace):
    def __getitem__(self, name):
        return self.disco


class NativeStickerPackSendTests(unittest.IsolatedAsyncioTestCase):
    def client(self, supported=True):
        info = Iq()
        query = ET.SubElement(info.xml, "{http://jabber.org/protocol/disco#info}query")
        if supported:
            ET.SubElement(
                query,
                "{http://jabber.org/protocol/disco#info}feature",
                {"var": "urn:can:sticker-pack:0"},
            )
        stanza = Message()
        stanza["id"] = "fixture"
        stanza.send = Mock()
        return PackClient(
            disco=SimpleNamespace(get_info=AsyncMock(return_value=info)),
            _mime_type_for_file=BridgeXmppClient._mime_type_for_file,
            _media_kind_from_mime_or_url=BridgeXmppClient._media_kind_from_mime_or_url,
            _upload_file=AsyncMock(return_value="https://upload.example.test/fixture.canstickers"),
            make_message=Mock(return_value=stanza),
            _append_file_metadata=BridgeXmppClient._append_file_metadata,
            _append_reply_metadata=BridgeXmppClient._append_reply_metadata,
            _message_body_for_display=BridgeXmppClient._message_body_for_display,
        ), stanza

    async def test_native_pack_has_explicit_mime_without_sticker_or_url_caption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.canstickers"
            path.write_bytes(fixture())
            client, stanza = self.client()
            sent = await BridgeXmppClient.send_file(
                client, "contact@whatsapp.example.test", str(path), as_sticker_pack=True
            )
            self.assertEqual(sent.media_mime, "application/x-can-sticker-pack")
            self.assertFalse(sent.is_sticker)
            self.assertEqual(sent.media_local_path, str(path))
            self.assertEqual(client._upload_file.call_args.kwargs["content_type"], sent.media_mime)
            metadata = stanza.xml.findall(".//{urn:xmpp:file:metadata:0}media-type")
            self.assertTrue(metadata)
            self.assertTrue(all(node.text == sent.media_mime for node in metadata))
            self.assertIsNone(stanza.xml.find("{urn:xmpp:stickers:0}sticker"))
            client.disco.get_info.assert_awaited_once_with(
                jid="whatsapp.example.test", cached=False, timeout=10
            )

    async def test_old_bridge_rejected_before_upload_or_send(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.canstickers"
            path.write_bytes(fixture())
            client, stanza = self.client(supported=False)
            with self.assertRaisesRegex(ValueError, "v30"):
                await BridgeXmppClient.send_file(
                    client, "contact@whatsapp.example.test", str(path), as_sticker_pack=True
                )
            client._upload_file.assert_not_awaited()
            stanza.send.assert_not_called()
