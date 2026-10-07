from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from xml.etree import ElementTree as ET

from slixmpp import Iq
from slixmpp import Message as Stanza

from cliente_xmpp.ui.conversation_panel import ConversationPanel
from cliente_xmpp.xmpp.client import (
    CAN_AUDIO_MODE_NS,
    MAX_AUDIO_ATTACHMENT_BYTES,
    BridgeXmppClient,
    UnsupportedAudioModeError,
    XmppService,
)
from cliente_xmpp.xmpp.events import FileBatchCompleted, MessageReceived


class AudioClient(SimpleNamespace):
    def __getitem__(self, name):
        return self.disco


class AudioAttachmentTests(unittest.IsolatedAsyncioTestCase):
    def client(self, supported=True):
        info = Iq()
        query = ET.SubElement(info.xml, "{http://jabber.org/protocol/disco#info}query")
        if supported:
            ET.SubElement(
                query, "{http://jabber.org/protocol/disco#info}feature", {"var": CAN_AUDIO_MODE_NS}
            )
        stanza = Stanza()
        stanza["id"] = "audio-fixture"
        stanza.send = Mock()
        return AudioClient(
            disco=SimpleNamespace(get_info=AsyncMock(return_value=info)),
            _mime_type_for_file=BridgeXmppClient._mime_type_for_file,
            _media_kind_from_mime_or_url=BridgeXmppClient._media_kind_from_mime_or_url,
            _upload_file=AsyncMock(return_value="https://upload.example.test/audio"),
            make_message=Mock(return_value=stanza),
            _append_file_metadata=BridgeXmppClient._append_file_metadata,
            _append_reply_metadata=BridgeXmppClient._append_reply_metadata,
            _message_body_for_display=BridgeXmppClient._message_body_for_display,
            _join_group_chat=Mock(),
        ), stanza

    async def test_attachments_preserve_bytes_mime_local_playback_and_reply(self):
        cases = (
            ("MP3", "audio"),
            ("wav", "document"),
            ("flac", "document"),
            ("m4a", "document"),
            ("ogg", "document"),
            ("opus", "document"),
            ("aac", "document"),
            ("weba", "document"),
            ("oga", "document"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension, mode in cases:
                with self.subTest(extension=extension):
                    path = Path(directory) / f"fixture.{extension}"
                    original = b"opaque audio fixture, never decode in this test"
                    path.write_bytes(original)
                    client, stanza = self.client()
                    with (
                        patch("cliente_xmpp.xmpp.client.convert_to_voice_note") as convert,
                        patch("cliente_xmpp.xmpp.client.media_duration_seconds", return_value=12.5),
                    ):
                        sent = await BridgeXmppClient.send_file(
                            client,
                            "room@whatsapp.example.test",
                            str(path),
                            is_group=True,
                            reply_to_jid="room@whatsapp.example.test/participant",
                            reply_to_id="remote-id",
                            reply_quote="Fictitious quoted text",
                        )
                    convert.assert_not_called()
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(client._upload_file.call_args.args, (path,))
                    self.assertEqual(
                        client._upload_file.call_args.kwargs["content_type"], sent.media_mime
                    )
                    self.assertEqual(stanza["thread"], f"{CAN_AUDIO_MODE_NS}:{mode}")
                    sharing = stanza.xml.find("{urn:xmpp:sfs:0}file-sharing")
                    self.assertEqual(
                        sharing.attrib["disposition"], "inline" if mode == "audio" else "attachment"
                    )
                    self.assertEqual(
                        sharing.find(".//{urn:xmpp:file:metadata:0}desc").text, "Audio file"
                    )
                    self.assertEqual(sent.media_kind, "audio")
                    self.assertEqual(sent.media_filename, path.name)
                    self.assertEqual(sent.audio_url, sent.media_url)
                    self.assertEqual(ConversationPanel._audio_source(sent), str(path))
                    player = SimpleNamespace(
                        _audio_source=ConversationPanel._audio_source,
                        _audio_player=SimpleNamespace(play=Mock(return_value="playing")),
                        _speaker=SimpleNamespace(speak=Mock()),
                        _schedule_audio_duration_update=Mock(),
                        _audio_autoplay_timer=SimpleNamespace(Start=Mock(), Stop=Mock()),
                    )
                    self.assertTrue(ConversationPanel._play_audio_message(player, sent, 3))
                    player._audio_player.play.assert_called_once_with(str(path))
                    player._audio_autoplay_timer.Start.assert_called_once_with(500)
                    self.assertEqual(player._current_audio_row_index, 3)
                    parsed = BridgeXmppClient._media_from_xml(stanza.xml)
                    self.assertEqual(parsed[1:4], ("audio", sent.media_mime, path.name))
                    self.assertEqual(parsed[5], 12.5)
                    reply = stanza.xml.find("{urn:xmpp:reply:0}reply")
                    self.assertEqual(reply.attrib["id"], "remote-id")
                    self.assertTrue(reply.attrib["to"].endswith("/participant"))

    async def test_old_bridge_or_disco_error_never_uploads_or_converts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.mp3"
            path.write_bytes(b"audio")
            for error in (None, RuntimeError("disco unavailable")):
                client, stanza = self.client(supported=False)
                client.disco.get_info.side_effect = error
                with patch("cliente_xmpp.xmpp.client.convert_to_voice_note") as convert:
                    with self.assertRaisesRegex(
                        (ValueError, RuntimeError), "v31|disco unavailable"
                    ):
                        await BridgeXmppClient.send_file(
                            client, "contact@whatsapp.example.test", str(path)
                        )
                convert.assert_not_called()
                client._upload_file.assert_not_awaited()
                stanza.send.assert_not_called()

    async def test_microphone_voice_mode_keeps_conversion_and_view_once_without_new_feature(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "recording.wav"
            source.write_bytes(b"recording")
            converted = Path(directory) / "ptt-fixture.ogg"
            converted.write_bytes(b"opus")
            client, stanza = self.client(supported=False)
            with (
                patch(
                    "cliente_xmpp.xmpp.client.convert_to_voice_note", return_value=converted
                ) as convert,
                patch("cliente_xmpp.xmpp.client.media_duration_seconds", return_value=3.0),
            ):
                sent = await BridgeXmppClient.send_file(
                    client,
                    "contact@whatsapp.example.test",
                    str(source),
                    as_voice_note=True,
                    view_once=True,
                )
            convert.assert_called_once_with(source)
            client.disco.get_info.assert_not_awaited()
            self.assertEqual(sent.media_mime, "audio/ogg; codecs=opus")
            self.assertEqual(stanza["thread"], "urn:marco-ml:whatsapp:view-once:0")
            self.assertEqual(
                stanza.xml.find(".//{urn:xmpp:file:metadata:0}desc").text, "Voice message"
            )

    async def test_invalid_modes_and_empty_attachment_rejected_before_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, payload, kwargs in (
                ("file.txt", b"text", {"as_voice_note": True}),
                ("file.mp3", b"audio", {"view_once": True}),
                ("empty.wav", b"", {}),
            ):
                path = Path(directory) / name
                path.write_bytes(payload)
                client, stanza = self.client()
                with self.assertRaises(ValueError):
                    await BridgeXmppClient.send_file(
                        client, "contact@whatsapp.example.test", str(path), **kwargs
                    )
                client._upload_file.assert_not_awaited()
                stanza.send.assert_not_called()

    async def test_other_files_do_not_require_audio_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension, kind in (("txt", "file"), ("png", "image"), ("mp4", "video")):
                path = Path(directory) / f"fixture.{extension}"
                path.write_bytes(b"fixture")
                client, stanza = self.client(supported=False)
                sent = await BridgeXmppClient.send_file(
                    client, "contact@whatsapp.example.test", str(path)
                )
                self.assertEqual(sent.media_kind, kind)
                self.assertFalse(stanza["thread"])
                client.disco.get_info.assert_not_awaited()

    async def test_native_xmpp_audio_uses_standard_metadata_without_private_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.wav"
            path.write_bytes(b"fixture")
            client, stanza = self.client(supported=False)
            with patch("cliente_xmpp.xmpp.client.media_duration_seconds", return_value=2.0):
                sent = await BridgeXmppClient.send_file(client, "contact@example.test", str(path))
            client.disco.get_info.assert_not_awaited()
            self.assertFalse(stanza["thread"])
            self.assertEqual(sent.media_kind, "audio")
            self.assertEqual(sent.media_local_path, str(path))

    async def test_upload_failure_does_not_delete_original_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ptt-fixture.ogg"
            path.write_bytes(b"original")
            client, stanza = self.client()
            client._upload_file.side_effect = OSError("upload failed")
            with (
                patch("cliente_xmpp.xmpp.client.media_duration_seconds", return_value=1.0),
                patch("cliente_xmpp.xmpp.client.delete_temporary_voice_note") as cleanup,
            ):
                with self.assertRaises(OSError):
                    await BridgeXmppClient.send_file(
                        client, "contact@whatsapp.example.test", str(path)
                    )
            cleanup.assert_not_called()
            self.assertEqual(path.read_bytes(), b"original")
            stanza.send.assert_not_called()

    async def test_oversize_audio_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.wav"
            path.write_bytes(b"fixture")
            client, stanza = self.client()
            with patch.object(
                Path, "stat", return_value=SimpleNamespace(st_size=MAX_AUDIO_ATTACHMENT_BYTES + 1)
            ):
                with self.assertRaisesRegex(ValueError, "64 MiB"):
                    await BridgeXmppClient.send_file(
                        client, "contact@whatsapp.example.test", str(path)
                    )
            client.disco.get_info.assert_not_awaited()
            client._upload_file.assert_not_awaited()
            stanza.send.assert_not_called()


class AudioModeServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_send_propagates_explicit_recording_flag(self):
        for voice in (False, True):
            completion = asyncio.get_running_loop().create_future()
            service = XmppService(completion.set_result)
            service._loop = asyncio.get_running_loop()
            service._client = SimpleNamespace(send_file=AsyncMock(return_value="fixture-message"))
            service.send_file("contact@whatsapp.example.test", "fixture.ogg", as_voice_note=voice)
            event = await asyncio.wait_for(completion, timeout=2)
            self.assertIsInstance(event, MessageReceived)
            self.assertEqual(service._client.send_file.call_args.kwargs["as_voice_note"], voice)

    async def test_batch_preserves_order_and_reports_capability_error_once_without_cleanup(self):
        events = []
        completion = asyncio.get_running_loop().create_future()

        def emit(event):
            events.append(event)
            if isinstance(event, FileBatchCompleted):
                completion.set_result(event)

        service = XmppService(emit)
        service._loop = asyncio.get_running_loop()
        service._client = SimpleNamespace(
            send_file=AsyncMock(
                side_effect=[
                    UnsupportedAudioModeError("Actualiza el puente a v31."),
                    "fixture-message",
                    UnsupportedAudioModeError("Actualiza el puente a v31."),
                ]
            )
        )
        with patch("cliente_xmpp.xmpp.client.delete_temporary_voice_note") as cleanup:
            service.send_files_serial(
                "contact@whatsapp.example.test", ["one.wav", "two.txt", "three.mp3"]
            )
            result = await asyncio.wait_for(completion, timeout=2)
        self.assertEqual(result.succeeded, 1)
        self.assertEqual(result.failed, 2)
        self.assertEqual(result.detail, "Actualiza el puente a v31.")
        cleanup.assert_not_called()
        self.assertEqual(
            [call.args[1] for call in service._client.send_file.call_args_list],
            ["one.wav", "two.txt", "three.mp3"],
        )
        self.assertTrue(
            all(
                not call.kwargs.get("as_voice_note", False)
                for call in service._client.send_file.call_args_list
            )
        )
        self.assertEqual(sum(isinstance(event, FileBatchCompleted) for event in events), 1)
