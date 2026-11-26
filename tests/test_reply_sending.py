from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

from slixmpp import Message as SlixMessage
from slixmpp.plugins.xep_0428 import stanza as fallback_stanza
from slixmpp.plugins.xep_0461 import stanza as reply_stanza

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.models.mentions import MentionReference
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.xmpp.client import XmppService


class ReplySendingTests(unittest.TestCase):
    @staticmethod
    def _status_bar() -> SimpleNamespace:
        return SimpleNamespace(SetStatusText=Mock())

    def test_existing_draft_keeps_reply_target_during_composer_clear(self) -> None:
        chat = Chat(jid="contact@example.test", name="Contacto")
        target = Message(
            chat_jid=chat.jid,
            sender_jid=chat.jid,
            body="mensaje original",
            message_id="whatsapp-message-id",
        )
        window = MainWindow.__new__(MainWindow)
        pending_messages: list[Message] = []
        reply_visible = True

        def consume_composed_message() -> str:
            window.reply_context = None
            return "borrador que ya estaba escrito"

        def clear_reply_quote() -> None:
            nonlocal reply_visible
            reply_visible = False

        window.reply_context = target
        window.edit_context = None
        window.current_jid = "me@example.test"
        window.conversation = SimpleNamespace(
            current_chat=chat,
            has_reply_context=lambda: reply_visible,
            consume_composed_message=consume_composed_message,
            clear_reply_quote=clear_reply_quote,
            focus_composer=Mock(),
        )
        window.status_bar = self._status_bar()
        window.xmpp = SimpleNamespace(send_reply=Mock(), send_message=Mock())
        window._require_whatsapp_connection = lambda: True
        window._mention_references_for_message = lambda _chat, _body: []
        window._add_pending_outgoing_message = pending_messages.append
        window._mark_current_chat_displayed = lambda _jid: None

        MainWindow._on_send_message(window, SimpleNamespace())

        window.xmpp.send_message.assert_not_called()
        window.xmpp.send_reply.assert_called_once()
        reply_args = window.xmpp.send_reply.call_args.args
        self.assertEqual(reply_args[:4], (
            chat.jid,
            "borrador que ya estaba escrito",
            target.sender_jid,
            target.message_id,
        ))
        self.assertEqual(pending_messages[0].reply_to_id, target.message_id)
        self.assertEqual(pending_messages[0].reply_quote, target.body)
        self.assertFalse(reply_visible)

    def test_media_reply_uses_the_visible_description_instead_of_the_internal_id(self) -> None:
        chat = Chat(jid="contact@example.test", name="Contacto")
        target = Message(
            chat_jid=chat.jid,
            sender_jid=chat.jid,
            body="",
            media_url="https://example.test/opaque-media-id",
            media_kind="image",
            media_filename="opaque-media-id",
            media_size=2048,
            message_id="media-message-id",
        )
        window = MainWindow.__new__(MainWindow)
        pending_messages: list[Message] = []
        window.reply_context = target
        window.edit_context = None
        window.current_jid = "me@example.test"
        window.conversation = SimpleNamespace(
            current_chat=chat,
            has_reply_context=lambda: True,
            consume_composed_message=lambda: "respuesta",
            clear_reply_quote=Mock(),
            focus_composer=Mock(),
        )
        window.status_bar = self._status_bar()
        window.xmpp = SimpleNamespace(send_reply=Mock(), send_message=Mock())
        window._require_whatsapp_connection = lambda: True
        window._mention_references_for_message = lambda _chat, _body: []
        window._add_pending_outgoing_message = pending_messages.append
        window._mark_current_chat_displayed = lambda _jid: None

        MainWindow._on_send_message(window, SimpleNamespace())

        self.assertEqual(pending_messages[0].reply_quote, "foto, 2.0 KB")
        window.xmpp.send_reply.assert_called_once()

    def test_copy_text_can_copy_the_downloaded_media_path(self) -> None:
        class Clipboard:
            def __init__(self) -> None:
                self.data: object | None = None

            def Open(self) -> bool:
                return True

            def Close(self) -> None:
                return None

            def SetData(self, data: object) -> None:
                self.data = data

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "document.pdf"
            path.write_bytes(b"document")
            message = Message(
                chat_jid="contact@example.test",
                sender_jid="contact@example.test",
                body="Archivo",
                media_url="https://example.test/document.pdf",
                media_kind="file",
                media_local_path=str(path),
            )
            window = MainWindow.__new__(MainWindow)
            window.status_bar = self._status_bar()
            clipboard = Clipboard()

            with (
                patch("cliente_xmpp.ui.main_window.wx.TheClipboard", clipboard),
                patch(
                    "cliente_xmpp.ui.main_window.wx.TextDataObject",
                    side_effect=lambda text: text,
                ),
            ):
                MainWindow._copy_message_text(window, message, copy_local_path=True)

            self.assertEqual(clipboard.data, message.media_local_path)
            window.status_bar.SetStatusText.assert_called_once_with("Ruta copiada")

    def test_recorded_audio_keeps_reply_target_and_quote(self) -> None:
        chat = Chat(jid="contact@example.test", name="Contacto")
        target = Message(
            chat_jid=chat.jid,
            sender_jid=chat.jid,
            body="mensaje original",
            message_id="whatsapp-message-id",
        )
        window = MainWindow.__new__(MainWindow)
        reply_visible = True
        window.reply_context = target
        window.current_jid = "me@example.test"
        window.conversation = SimpleNamespace(
            current_chat=chat,
            has_reply_context=lambda: reply_visible,
            view_once_audio=SimpleNamespace(GetValue=lambda: False),
            set_recording_state=Mock(),
            focus_composer=Mock(),
        )
        window.audio_recorder = SimpleNamespace(stop_and_save=lambda: "ptt.ogg")
        window.status_bar = self._status_bar()
        window.xmpp = SimpleNamespace(send_chat_state=Mock(), send_file=Mock())
        window._mark_current_chat_displayed = Mock()
        window._cancel_reply = Mock()

        MainWindow._stop_recording_and_send(window)

        window.xmpp.send_file.assert_called_once_with(
            chat.jid,
            "ptt.ogg",
            is_group=False,
            view_once=False,
            as_voice_note=True,
            reply_to_jid=target.sender_jid,
            reply_to_id=target.message_id,
            reply_quote=target.body,
        )
        window._cancel_reply.assert_called_once_with()

    def test_file_attachment_keeps_reply_target_and_quote(self) -> None:
        chat = Chat(jid="contact@example.test", name="Contacto")
        target = Message(
            chat_jid=chat.jid,
            sender_jid=chat.jid,
            body="mensaje original",
            message_id="whatsapp-message-id",
        )
        window = MainWindow.__new__(MainWindow)
        window.reply_context = target
        window.current_jid = "me@example.test"
        window.conversation = SimpleNamespace(
            has_reply_context=lambda: True,
            clear_reply_quote=Mock(),
            focus_composer=Mock(),
        )
        window.status_bar = self._status_bar()
        window.xmpp = SimpleNamespace(send_file=Mock())
        window._require_whatsapp_connection = lambda: True
        window._set_clipboard_status = Mock()
        window._mark_current_chat_displayed = Mock()
        window._cancel_reply = Mock()

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "foto.jpg"
            path.write_bytes(b"image")
            MainWindow._send_files_to_chat(window, chat, [path], source_label="imagen")

        window.xmpp.send_file.assert_called_once_with(
            chat.jid,
            str(path),
            is_group=False,
            reply_to_jid=target.sender_jid,
            reply_to_id=target.message_id,
            reply_quote=target.body,
        )
        window._cancel_reply.assert_called_once_with()

    def test_group_reply_targets_room_occupant_instead_of_private_contact(self) -> None:
        chat = Chat(
            jid="#120363216552048055@whatsapp.example.test",
            name="Grupo",
            is_group=True,
        )
        target = Message(
            chat_jid=chat.jid,
            sender_jid="+5214493860911@whatsapp.example.test",
            sender_name="Marquiños",
            body="mensaje original",
            message_id="whatsapp-message-id",
            chat_is_group=True,
        )

        reply_to = MainWindow._reply_target_jid(
            chat,
            target,
            "angel@example.test",
        )

        self.assertEqual(reply_to, f"{chat.jid}/Marquiños")

    def test_group_reply_to_own_message_uses_own_room_nick(self) -> None:
        chat = Chat(
            jid="#120363216552048055@whatsapp.example.test",
            name="Grupo",
            is_group=True,
        )
        target = Message(
            chat_jid=chat.jid,
            sender_jid="me",
            sender_name="Tú",
            body="mensaje propio",
            outgoing=True,
            message_id="whatsapp-message-id",
            chat_is_group=True,
        )

        reply_to = MainWindow._reply_target_jid(
            chat,
            target,
            "angel@example.test",
        )

        self.assertEqual(reply_to, f"{chat.jid}/angel")

    def test_private_reply_quote_navigates_back_to_the_group_message(self) -> None:
        group = Chat(
            jid="#120363216552048055@whatsapp.example.test",
            name="Grupo",
            is_group=True,
        )
        private_chat = Chat(jid="+524491234567@whatsapp.example.test", name="Marquiños")
        source = Message(
            chat_jid=group.jid,
            sender_jid="+524491234567@whatsapp.example.test",
            body="Mensaje del grupo",
            message_id="group-message-id",
            chat_is_group=True,
        )
        reply = Message(
            chat_jid=private_chat.jid,
            sender_jid="me",
            body="Respuesta privada",
            outgoing=True,
            reply_to_jid=f"{group.jid}/Marquiños",
            reply_to_id=source.message_id,
        )
        window = MainWindow.__new__(MainWindow)
        window.conversation = SimpleNamespace(
            current_chat=private_chat,
            find_message_by_id=lambda _message_id: None,
        )
        window.messages_by_chat = {group.jid: [source]}
        window._chat_by_jid = lambda chat_jid: group if chat_jid == group.jid else None

        target = MainWindow._quoted_message_navigation_target(window, reply)

        self.assertEqual(target, (group, source))

    def test_private_reply_sends_to_contact_and_keeps_group_reply_target(self) -> None:
        private_chat = Chat(jid="+524491234567@whatsapp.example.test", name="Rabanita")
        reply_context = Message(
            chat_jid=private_chat.jid,
            sender_jid="#group@whatsapp.example.test/Rabanita",
            body="Mensaje del grupo",
            message_id="group-message-id",
        )
        window = MainWindow.__new__(MainWindow)
        pending_messages: list[Message] = []
        window.reply_context = reply_context
        window.edit_context = None
        window.current_jid = "me@example.test"
        window.conversation = SimpleNamespace(
            current_chat=private_chat,
            has_reply_context=lambda: True,
            consume_composed_message=lambda: "Respuesta privada",
            clear_reply_quote=Mock(),
            focus_composer=Mock(),
        )
        window.status_bar = self._status_bar()
        window.xmpp = SimpleNamespace(send_reply=Mock(), send_message=Mock())
        window._require_whatsapp_connection = lambda: True
        window._mention_references_for_message = lambda _chat, _body: []
        window._add_pending_outgoing_message = pending_messages.append
        window._mark_current_chat_displayed = lambda _jid: None

        MainWindow._on_send_message(window, SimpleNamespace())

        window.xmpp.send_reply.assert_called_once()
        reply_args = window.xmpp.send_reply.call_args.args
        self.assertEqual(reply_args[:4], (
            private_chat.jid,
            "Respuesta privada",
            reply_context.sender_jid,
            reply_context.message_id,
        ))
        self.assertEqual(pending_messages[0].chat_jid, private_chat.jid)
        self.assertEqual(pending_messages[0].reply_to_jid, reply_context.sender_jid)
        self.assertEqual(pending_messages[0].reply_to_id, reply_context.message_id)

    def test_private_reply_can_load_group_quote_after_restart(self) -> None:
        group = Chat(
            jid="#120363216552048055@whatsapp.example.test",
            name="Grupo",
            is_group=True,
        )
        private_chat = Chat(jid="+524491234567@whatsapp.example.test", name="Rabanita")
        reply = Message(
            chat_jid=private_chat.jid,
            sender_jid="me",
            body="Respuesta privada",
            outgoing=True,
            reply_to_jid=f"{group.jid}/Rabanita",
            reply_to_id="group-message-id",
        )
        window = MainWindow.__new__(MainWindow)
        window.conversation = SimpleNamespace(
            current_chat=private_chat,
            find_message_by_id=lambda _message_id: None,
        )
        window.messages_by_chat = {group.jid: []}
        window._chat_by_jid = lambda chat_jid: group if chat_jid == group.jid else None
        window._load_quoted_group_message_async = Mock()
        window.status_bar = self._status_bar()

        self.assertTrue(MainWindow._can_go_to_quoted_message(window, reply))
        MainWindow._go_to_quoted_message(window, reply)

        window._load_quoted_group_message_async.assert_called_once_with(
            group,
            reply.reply_to_id,
        )

    def test_group_quote_navigation_finds_displayed_marker_id_in_memory(self) -> None:
        group = Chat(
            jid="#120363216552048055@whatsapp.example.test",
            name="Grupo",
            is_group=True,
        )
        target = Message(
            chat_jid=group.jid,
            sender_jid="member@example.test",
            body="mensaje original",
            message_id="internal-id",
            displayed_marker_id="whatsapp-id",
            chat_is_group=True,
        )
        reply = Message(
            chat_jid=group.jid,
            sender_jid="member@example.test",
            body="respuesta",
            reply_to_jid=group.jid,
            reply_to_id="whatsapp-id",
            chat_is_group=True,
        )
        window = MainWindow.__new__(MainWindow)
        window.messages_by_chat = {group.jid: [target, reply]}
        window.conversation = SimpleNamespace(current_chat=group)
        window._chat_by_jid = lambda chat_jid: group if chat_jid == group.jid else None

        self.assertEqual(
            MainWindow._quoted_message_navigation_target(window, reply),
            (group, target),
        )

    def test_group_reply_without_occupant_or_nick_is_rejected(self) -> None:
        chat = Chat(
            jid="#120363216552048055@whatsapp.example.test",
            name="Grupo",
            is_group=True,
        )
        target = Message(
            chat_jid=chat.jid,
            sender_jid="+5214493860911@whatsapp.example.test",
            body="mensaje sin nick",
            message_id="whatsapp-message-id",
            chat_is_group=True,
        )
        window = MainWindow.__new__(MainWindow)
        window.reply_context = None
        window.edit_context = None
        window.conversation = SimpleNamespace(
            current_chat=chat,
            insert_reply_quote=Mock(),
        )
        window.status_bar = self._status_bar()
        window._require_whatsapp_connection = lambda: True

        MainWindow._reply_to_message(window, target)

        self.assertIsNone(window.reply_context)
        window.conversation.insert_reply_quote.assert_not_called()
        status = window.status_bar.SetStatusText.call_args.args[0]
        self.assertIn("identificar al participante", status)

    def test_pending_local_message_cannot_be_selected_as_reply_target(self) -> None:
        chat = Chat(jid="contact@example.test", name="Contacto")
        target = Message(
            chat_jid=chat.jid,
            sender_jid="me",
            body="todavía enviándose",
            outgoing=True,
            message_id="cliente-xmpp-temporary-id",
            delivery_state="pending",
        )
        window = MainWindow.__new__(MainWindow)
        window.reply_context = None
        window.edit_context = None
        window.conversation = SimpleNamespace(
            current_chat=chat,
            insert_reply_quote=Mock(),
        )
        window.status_bar = self._status_bar()
        window._require_whatsapp_connection = lambda: True

        MainWindow._reply_to_message(window, target)

        self.assertIsNone(window.reply_context)
        window.conversation.insert_reply_quote.assert_not_called()
        status = window.status_bar.SetStatusText.call_args.args[0]
        self.assertIn("todavía se está enviando", status)

    def test_confirmed_own_message_can_be_selected_as_reply_target(self) -> None:
        chat = Chat(jid="contact@example.test", name="Contacto")
        target = Message(
            chat_jid=chat.jid,
            sender_jid="me",
            body="mensaje propio confirmado",
            outgoing=True,
            message_id="cliente-xmpp-confirmed-id",
            delivery_state="sent",
        )
        window = MainWindow.__new__(MainWindow)
        window.reply_context = None
        window.edit_context = None
        window.conversation = SimpleNamespace(
            current_chat=chat,
            insert_reply_quote=Mock(),
        )
        window.status_bar = self._status_bar()
        window._require_whatsapp_connection = lambda: True

        MainWindow._reply_to_message(window, target)

        self.assertIs(window.reply_context, target)
        window.conversation.insert_reply_quote.assert_called_once_with(target)
        window.status_bar.SetStatusText.assert_called_once_with("Respuesta preparada")

    def test_visible_reply_without_target_does_not_send_plain_message(self) -> None:
        chat = Chat(jid="contact@example.test", name="Contacto")
        window = MainWindow.__new__(MainWindow)
        window.reply_context = None
        window.edit_context = None
        window.conversation = SimpleNamespace(
            current_chat=chat,
            has_reply_context=lambda: True,
            consume_composed_message=Mock(return_value="no debe salir"),
            clear_reply_quote=Mock(),
            focus_composer=Mock(),
        )
        window.status_bar = self._status_bar()
        window.xmpp = SimpleNamespace(send_reply=Mock(), send_message=Mock())
        window._require_whatsapp_connection = lambda: True

        MainWindow._on_send_message(window, SimpleNamespace())

        window.conversation.consume_composed_message.assert_not_called()
        window.xmpp.send_reply.assert_not_called()
        window.xmpp.send_message.assert_not_called()
        window.conversation.clear_reply_quote.assert_called_once()


_CLIENT_NS = "jabber:client"
_REPLY_NS = "urn:xmpp:reply:0"
_FALLBACK_NS = "urn:xmpp:fallback:0"
_REFERENCE_NS = "urn:xmpp:reference:0"


class _StanzaMessage:
    """Minimal outgoing stanza whose XML a real slixmpp Message can re-read."""

    def __init__(self, to_jid: str, body: str, message_type: str) -> None:
        self.xml = ET.Element(
            f"{{{_CLIENT_NS}}}message", {"to": to_jid, "type": message_type}
        )
        ET.SubElement(self.xml, f"{{{_CLIENT_NS}}}body").text = body
        self.sent = False

    def __getitem__(self, key: str) -> str:
        if key == "body":
            return self.xml.findtext(f"{{{_CLIENT_NS}}}body") or ""
        raise KeyError(key)

    def __setitem__(self, key: str, value: object) -> None:
        if key == "id":
            self.xml.set("id", str(value))
        elif key == "body":
            body = self.xml.find(f"{{{_CLIENT_NS}}}body")
            assert body is not None
            body.text = str(value)

    def append(self, node: ET.Element) -> None:
        self.xml.append(node)

    def enable(self, plugin: str) -> None:
        raise KeyError(plugin)

    def send(self) -> None:
        self.sent = True


class _StanzaClient:
    def __init__(self) -> None:
        self.message: _StanzaMessage | None = None

    def make_message(self, mto: str, mbody: str, mtype: str) -> _StanzaMessage:
        self.message = _StanzaMessage(mto, mbody, mtype)
        return self.message

    @staticmethod
    def track_transient_message_retry(*_args: object) -> None:
        return None

    @staticmethod
    def _join_group_chat(_jid: str) -> None:
        return None


class _ImmediateLoop:
    @staticmethod
    def call_soon_threadsafe(callback: object) -> None:
        callback()


def _bridge_view(xml: ET.Element) -> tuple[str, str, str, str]:
    """Read the stanza as the bridge does: reply id/to, quoted body and stripped text."""
    fallback_stanza.register_plugins()
    reply_stanza.register_plugins()
    msg = SlixMessage(xml=ET.fromstring(ET.tostring(xml)))
    assert "reply" in msg and "fallback" in msg
    # Same normalisation as the bridge's strip_quote_prefix for ReplyBody.
    quote = "\n".join(
        line.lstrip(">").strip()
        for line in msg["reply"].get_fallback_body().split("\n")
    ).strip()
    text = msg["fallback"].get_stripped_body(_REPLY_NS)
    return msg["reply"]["id"], msg["reply"]["to"], quote, text


class ReplyQuoteReachesBridgeTests(unittest.TestCase):
    """WhatsApp builds the quote from the fallback body, not from the reply id alone."""

    @staticmethod
    def _window(chat: Chat, target: Message, body: str) -> tuple[MainWindow, list[Message]]:
        window = MainWindow.__new__(MainWindow)
        pending_messages: list[Message] = []
        window.reply_context = target
        window.edit_context = None
        window.current_jid = "yo@example.test"
        window.conversation = SimpleNamespace(
            current_chat=chat,
            has_reply_context=lambda: True,
            consume_composed_message=lambda: body,
            clear_reply_quote=Mock(),
            focus_composer=Mock(),
        )
        window.status_bar = SimpleNamespace(SetStatusText=Mock())
        window.xmpp = SimpleNamespace(send_reply=Mock(), send_message=Mock())
        window._require_whatsapp_connection = lambda: True
        window._mention_references_for_message = lambda _chat, _body: []
        window._add_pending_outgoing_message = pending_messages.append
        window._mark_current_chat_displayed = lambda _jid: None
        return window, pending_messages

    def test_direct_text_reply_passes_the_quoted_body_to_the_service(self) -> None:
        chat = Chat(jid="+10000000001@whatsapp.example.test", name="Contacto ficticio")
        target = Message(
            chat_jid=chat.jid,
            sender_jid=chat.jid,
            body="¿Quedamos mañana?",
            message_id="3EB0FICTICIO0001",
        )
        window, pending = self._window(chat, target, "Sí, a las diez")

        MainWindow._on_send_message(window, SimpleNamespace())

        window.xmpp.send_reply.assert_called_once()
        call = window.xmpp.send_reply.call_args
        self.assertEqual(call.args[2:4], (chat.jid, "3EB0FICTICIO0001"))
        self.assertEqual(call.kwargs["reply_quote"], "¿Quedamos mañana?")
        self.assertEqual(pending[0].body, "Sí, a las diez")

    def test_group_reply_to_own_echo_uses_the_room_stanza_id(self) -> None:
        chat = Chat(jid="#grupo-ficticio@whatsapp.example.test", name="Grupo", is_group=True)
        # The echo keeps the local origin id; only its stanza-id maps to WhatsApp.
        target = Message(
            chat_jid=chat.jid,
            sender_jid="Yo",
            body="Mensaje propio",
            outgoing=True,
            message_id="cliente-xmpp-origen-ficticio",
            displayed_marker_id="3EB0FICTICIO0002",
            chat_is_group=True,
            delivery_state="sent",
        )
        window, pending = self._window(chat, target, "Respuesta en el grupo")

        MainWindow._on_send_message(window, SimpleNamespace())

        call = window.xmpp.send_reply.call_args
        self.assertEqual(call.args[2:4], (f"{chat.jid}/yo", "3EB0FICTICIO0002"))
        self.assertTrue(call.kwargs["is_group"])
        self.assertEqual(call.kwargs["reply_quote"], "Mensaje propio")
        self.assertEqual(pending[0].reply_to_id, "3EB0FICTICIO0002")

    def test_group_reply_without_stanza_id_keeps_the_remote_message_id(self) -> None:
        chat = Chat(jid="#grupo-ficticio@whatsapp.example.test", name="Grupo", is_group=True)
        target = Message(
            chat_jid=chat.jid,
            sender_jid=f"{chat.jid}/Participante",
            sender_name="Participante",
            body="Hola",
            message_id="3EB0FICTICIO0003",
            chat_is_group=True,
        )

        self.assertEqual(
            MainWindow._reply_target_id(chat, target, f"{chat.jid}/Participante"),
            "3EB0FICTICIO0003",
        )

    def test_private_reply_to_group_message_uses_the_group_stanza_id(self) -> None:
        private_chat = Chat(jid="+10000000002@whatsapp.example.test", name="Participante")
        reply_to_jid = "#grupo-ficticio@whatsapp.example.test/Participante"
        target = Message(
            chat_jid=private_chat.jid,
            sender_jid=reply_to_jid,
            body="Mensaje del grupo",
            message_id="origen-ficticio",
            displayed_marker_id="3EB0FICTICIO0004",
        )

        self.assertEqual(
            MainWindow._reply_target_id(private_chat, target, reply_to_jid),
            "3EB0FICTICIO0004",
        )

    def test_direct_reply_never_uses_a_marker_id(self) -> None:
        chat = Chat(jid="+10000000001@whatsapp.example.test", name="Contacto")
        target = Message(
            chat_jid=chat.jid,
            sender_jid=chat.jid,
            body="Hola",
            message_id="3EB0FICTICIO0005",
            displayed_marker_id="no-debe-usarse",
        )

        self.assertEqual(
            MainWindow._reply_target_id(chat, target, chat.jid), "3EB0FICTICIO0005"
        )

    def _send(
        self,
        *,
        to_jid: str,
        is_group: bool,
        reply_to_jid: str,
        mentions: list[MentionReference] | None = None,
    ) -> _StanzaMessage:
        service = XmppService(lambda _event: None)
        client = _StanzaClient()
        service._client = client
        service._loop = _ImmediateLoop()
        service.send_reply(
            to_jid,
            "Sí, a las diez",
            reply_to_jid,
            "3EB0FICTICIO0006",
            is_group=is_group,
            message_id="cliente-xmpp-respuesta-ficticia",
            mentions=mentions,
            reply_quote="¿Quedamos\nmañana?",
        )
        assert client.message is not None
        self.assertTrue(client.message.sent)
        return client.message

    def test_direct_reply_stanza_carries_id_target_and_quote_for_the_bridge(self) -> None:
        contact = "+10000000001@whatsapp.example.test"
        message = self._send(to_jid=contact, is_group=False, reply_to_jid=contact)

        reply_id, reply_to, quote, text = _bridge_view(message.xml)

        self.assertEqual(reply_id, "3EB0FICTICIO0006")
        self.assertEqual(reply_to, contact)
        self.assertEqual(quote, "¿Quedamos\nmañana?")
        self.assertEqual(text, "Sí, a las diez")
        self.assertEqual(message["body"], "> ¿Quedamos\n> mañana?\nSí, a las diez")
        self.assertEqual(len(message.xml.findall(f"{{{_FALLBACK_NS}}}fallback")), 1)

    def test_group_reply_stanza_keeps_room_occupant_and_mention_offsets(self) -> None:
        room = "#grupo-ficticio@whatsapp.example.test"
        mention = MentionReference(
            participant_jid="+10000000003@whatsapp.example.test", start=0, end=2
        )
        message = self._send(
            to_jid=room,
            is_group=True,
            reply_to_jid=f"{room}/Participante",
            mentions=[mention],
        )

        reply_id, reply_to, quote, text = _bridge_view(message.xml)

        self.assertEqual(message.xml.attrib["type"], "groupchat")
        self.assertEqual(reply_id, "3EB0FICTICIO0006")
        self.assertEqual(reply_to, f"{room}/Participante")
        self.assertEqual(quote, "¿Quedamos\nmañana?")
        # The bridge reads mention offsets against the text without the quote.
        reference = message.xml.find(f"{{{_REFERENCE_NS}}}reference")
        assert reference is not None
        begin, end = int(reference.attrib["begin"]), int(reference.attrib["end"])
        self.assertEqual(text[begin:end], "Sí")

    def test_reply_without_quote_still_sends_only_the_reply_reference(self) -> None:
        service = XmppService(lambda _event: None)
        client = _StanzaClient()
        service._client = client
        service._loop = _ImmediateLoop()

        service.send_reply("contact@example.test", "ok", "contact@example.test", "id-1")

        assert client.message is not None
        reply = client.message.xml.find(f"{{{_REPLY_NS}}}reply")
        assert reply is not None
        self.assertEqual(reply.attrib["id"], "id-1")
        self.assertIsNone(client.message.xml.find(f"{{{_FALLBACK_NS}}}fallback"))
        self.assertEqual(client.message["body"], "ok")


if __name__ == "__main__":
    unittest.main()
