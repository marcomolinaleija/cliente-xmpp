"""Regresiones de reacciones en vivo, citas de fotos, atajos y chats nuevos."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

import wx

from cliente_xmpp.media.downloads import humanize_reply_quote, is_technical_media_filename
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.models.phone_numbers import NormalizedPhoneNumber
from cliente_xmpp.models.reactions import ReactionState, ReactionUpdate
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.xmpp.client import REACTIONS_NS, BridgeXmppClient
from cliente_xmpp.xmpp.events import WhatsAppPhoneResolved

ACCOUNT = "yo@example.test"
CHAT = "+10000000001@whatsapp.example.test"
GROUP = "#120000000000000001@whatsapp.example.test"
HASH = "3eb0a1b2c3d4e5f60718293a4b5c6d7e8f901234"


class _Stanza:
    def __init__(self, xml: ET.Element) -> None:
        self.xml = xml

    def __getitem__(self, key: str) -> object:
        if key == "type":
            return self.xml.get("type", "normal")
        raise KeyError(key)


def _reaction(message_type: str, *, body: str = "") -> _Stanza:
    xml = ET.Element("{jabber:client}message", {"type": message_type})
    if body:
        ET.SubElement(xml, "{jabber:client}body").text = body
    reactions = ET.SubElement(xml, f"{{{REACTIONS_NS}}}reactions", {"id": "target"})
    ET.SubElement(reactions, f"{{{REACTIONS_NS}}}reaction").text = "👍"
    return _Stanza(xml)


class LiveReactionRoutingTests(unittest.TestCase):
    def test_bodyless_reactions_reach_the_chat_and_group_handlers(self) -> None:
        client = SimpleNamespace(_on_message=Mock(), _on_groupchat_message=Mock())
        BridgeXmppClient._on_bodyless_reaction(client, _reaction("chat"))
        BridgeXmppClient._on_bodyless_reaction(client, _reaction("groupchat"))
        client._on_message.assert_called_once()
        client._on_groupchat_message.assert_called_once()

    def test_reaction_with_fallback_body_is_left_to_the_regular_handler(self) -> None:
        client = SimpleNamespace(_on_message=Mock(), _on_groupchat_message=Mock())
        BridgeXmppClient._on_bodyless_reaction(client, _reaction("chat", body="👍"))
        BridgeXmppClient._on_bodyless_reaction(client, _reaction("error"))
        client._on_message.assert_not_called()
        client._on_groupchat_message.assert_not_called()

    def test_removal_keeps_an_empty_state_for_storage(self) -> None:
        target = Message(chat_jid=CHAT, sender_jid=CHAT, body="Hola", message_id="target",
                         reaction_states=(ReactionState("ari", ("👍",)),), reactions=("👍",))
        MainWindow._apply_reaction_states(
            target, ReactionUpdate(chat_jid=CHAT, target_id="target", sender_id="ari")
        )
        self.assertEqual(target.reactions, ())
        self.assertEqual(target.reaction_states, (ReactionState("ari", ()),))

    def test_history_copy_without_reactions_does_not_erase_stored_ones(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "messages.sqlite3")
            base = dict(chat_jid=CHAT, sender_jid=CHAT, body="Hola", message_id="target")
            reacted = Message(**base, reactions=("❤️",),
                              reaction_states=(ReactionState("ari", ("❤️",)),))
            store.upsert_messages(ACCOUNT, [reacted])
            store.upsert_messages(ACCOUNT, [Message(**base)])  # MAM/inbox copy
            self.assertEqual(store.load_message_by_id(ACCOUNT, CHAT, "target").reactions,
                             ("❤️",))

            removed = Message(**base, reaction_states=(ReactionState("ari", ()),))
            store.upsert_messages(ACCOUNT, [removed])
            self.assertEqual(store.load_message_by_id(ACCOUNT, CHAT, "target").reactions, ())

    def test_reaction_to_an_unloaded_message_is_persisted_in_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "messages.sqlite3")
            store.upsert_messages(ACCOUNT, [Message(chat_jid=GROUP, sender_jid=f"{GROUP}/Ari",
                                                    body="Hola", message_id="bridge-id",
                                                    displayed_marker_id="room-id",
                                                    chat_is_group=True)])
            window = SimpleNamespace(
                current_jid=ACCOUNT, message_store=store, _storage_reset_in_progress=False,
                _queue_storage_write=lambda operation, *args: operation(*args),
            )
            MainWindow._persist_reaction_for_stored_message(window, ReactionUpdate(
                chat_jid=GROUP, target_id="room-id", sender_id="ari", reactions=("🔥",),
                is_group=True,
            ))
            self.assertEqual(store.load_message_by_id(ACCOUNT, GROUP, "room-id").reactions,
                             ("🔥",))


class ReadableImageQuoteTests(unittest.TestCase):
    def test_technical_photo_names_become_a_readable_reference(self) -> None:
        self.assertTrue(is_technical_media_filename(f"{HASH}.jpg"))
        self.assertTrue(is_technical_media_filename("sticker-0123456789abcdef0123.webp"))
        self.assertFalse(is_technical_media_filename("Informe anual.pdf"))
        cases = {
            f"Foto: {HASH}.jpg, 234.2 KB": "foto, 234.2 KB",
            f"Foto: {HASH.upper()}.jpeg": "foto",
            f"Video: {HASH}.mp4, 2.0 MB": "video, 2.0 MB",
            "Archivo: Informe anual.pdf, 79.4 KB": "Archivo: Informe anual.pdf, 79.4 KB",
            "Nos vemos mañana": "Nos vemos mañana",
        }
        for quote, expected in cases.items():
            with self.subTest(quote=quote):
                self.assertEqual(humanize_reply_quote(quote), expected)

    def test_hydrated_quote_of_a_captionless_photo_has_no_hash(self) -> None:
        photo = Message(chat_jid=CHAT, sender_jid=CHAT, body=f"Foto: {HASH}.jpg, 50.1 KB",
                        message_id="photo", media_url=f"https://files.example.test/{HASH}.jpg",
                        media_kind="image", media_size=51_302)
        reply = Message(chat_jid=CHAT, sender_jid="yo", body="¡Qué bonita!", outgoing=True,
                        message_id="reply", reply_to_id="photo")
        MainWindow._hydrate_reply_quotes([photo, reply])
        self.assertEqual(reply.reply_quote, "foto, 50.1 KB")
        self.assertNotIn(HASH, reply.reply_quote)


class _Key:
    def __init__(self, code: int, *, ctrl: bool = False, alt: bool = False,
                 shift: bool = False) -> None:
        self.code, self.ctrl, self.alt, self.shift = code, ctrl, alt, shift
        self.skipped = False

    def GetKeyCode(self) -> int:
        return self.code

    def GetUnicodeKey(self) -> int:
        return self.code

    def ControlDown(self) -> bool:
        return self.ctrl

    def AltDown(self) -> bool:
        return self.alt

    def ShiftDown(self) -> bool:
        return self.shift

    def Skip(self) -> None:
        self.skipped = True


class MessageListShortcutTests(unittest.TestCase):
    def test_only_plain_ctrl_c_and_ctrl_d_are_list_shortcuts(self) -> None:
        self.assertEqual(MainWindow._message_list_shortcut(_Key(ord("C"), ctrl=True)), "copy")
        self.assertEqual(MainWindow._message_list_shortcut(_Key(ord("D"), ctrl=True)),
                         "download")
        for key in (_Key(ord("C")), _Key(ord("C"), ctrl=True, shift=True),
                    _Key(ord("D"), ctrl=True, alt=True), _Key(ord("P"), ctrl=True)):
            self.assertIsNone(MainWindow._message_list_shortcut(key))

    def _window(self, message: Message | None) -> SimpleNamespace:
        window = SimpleNamespace(
            conversation=SimpleNamespace(selected_message=lambda: message,
                                         selected_messages=lambda: [message] if message else []),
            status_bar=SimpleNamespace(SetStatusText=Mock()),
            speaker=SimpleNamespace(speak=Mock()),
            _copy_message_text=Mock(return_value=True),
            _copy_media_file=Mock(return_value=True),
            _download_media=Mock(),
            _describe_media_with_rayoai=Mock(),
            _copy_selected_messages=Mock(),
            _message_timestamp=lambda item: item.sent_at,
        )
        window._announce_status = lambda text: MainWindow._announce_status(window, text)
        window._selected_messages_in_order = (
            lambda: MainWindow._selected_messages_in_order(window)
        )
        return window

    def test_ctrl_c_copies_text_or_the_attachment(self) -> None:
        text = Message(chat_jid=CHAT, sender_jid=CHAT, body="Hola", message_id="t")
        window = self._window(text)
        MainWindow._run_message_list_shortcut(window, "copy")
        window._copy_message_text.assert_called_once_with(text)

        photo = Message(chat_jid=CHAT, sender_jid=CHAT, body="", message_id="p",
                        media_url="https://files.example.test/a.jpg", media_kind="image")
        window = self._window(photo)
        with patch("cliente_xmpp.ui.main_window.local_media_path", return_value=None):
            MainWindow._run_message_list_shortcut(window, "copy")
        window._download_media.assert_called_once_with(photo, copy_after_download=True)
        with patch("cliente_xmpp.ui.main_window.local_media_path", return_value=Path("a.jpg")):
            MainWindow._run_message_list_shortcut(window, "copy")
        window._copy_media_file.assert_called_once_with(photo)

    def test_ctrl_d_describes_images_and_downloads_other_files(self) -> None:
        photo = Message(chat_jid=CHAT, sender_jid=CHAT, body="", message_id="p",
                        media_url="https://files.example.test/a.jpg", media_kind="image")
        window = self._window(photo)
        MainWindow._run_message_list_shortcut(window, "download")
        window._describe_media_with_rayoai.assert_called_once_with(photo)

        document = Message(chat_jid=CHAT, sender_jid=CHAT, body="", message_id="d",
                           media_url="https://files.example.test/a.pdf", media_kind="file")
        window = self._window(document)
        with patch("cliente_xmpp.ui.main_window.local_media_path", return_value=None):
            MainWindow._run_message_list_shortcut(window, "download")
        window._download_media.assert_called_once_with(document)

        text = Message(chat_jid=CHAT, sender_jid=CHAT, body="Hola", message_id="t")
        window = self._window(text)
        MainWindow._run_message_list_shortcut(window, "download")
        window._download_media.assert_not_called()
        window.speaker.speak.assert_called_once()


class VoiceNoteEnterTests(unittest.TestCase):
    def _window(self, plays: bool) -> SimpleNamespace:
        return SimpleNamespace(
            conversation=SimpleNamespace(
                selected_message=lambda: None,
                message_selection_mode=False,
                focus_quoted_message_button=lambda: False,
                speak_selected_text_message=lambda: False,
                cycle_selected_audio_speed=lambda: None,
                play_selected_video=lambda: False,
                play_selected_audio=Mock(return_value=plays),
                open_selected_message_reader=Mock(return_value=True),
                current_chat=None,
            ),
            _delete_selected_message=lambda: False,
            _is_open_link_shortcut=lambda _event: False,
            status_bar=SimpleNamespace(SetStatusText=Mock()),
        )

    def test_enter_plays_a_voice_note_instead_of_opening_the_reader(self) -> None:
        window = self._window(plays=True)
        MainWindow._on_messages_key_down(window, _Key(wx.WXK_RETURN))
        window.conversation.play_selected_audio.assert_called_once()
        window.conversation.open_selected_message_reader.assert_not_called()

    def test_enter_on_text_still_opens_the_reader(self) -> None:
        window = self._window(plays=False)
        MainWindow._on_messages_key_down(window, _Key(wx.WXK_RETURN))
        window.conversation.open_selected_message_reader.assert_called_once()


class NewChatResolutionTests(unittest.TestCase):
    PHONE = NormalizedPhoneNumber(e164="+10000000001", international="+1 000-000-0001")

    def _window(self) -> SimpleNamespace:
        window = SimpleNamespace(
            whatsapp_component_jid="whatsapp.example.test",
            _chat_by_jid=lambda _jid: None,
            _open_chat=Mock(),
            _open_chat_for_phone=Mock(return_value=Chat(jid=CHAT, name="x")),
            xmpp=SimpleNamespace(resolve_whatsapp_phone=Mock()),
            status_bar=SimpleNamespace(SetStatusText=Mock()),
            speaker=SimpleNamespace(speak=Mock()),
        )
        window._announce_status = lambda text: MainWindow._announce_status(window, text)
        return window

    def test_unknown_number_is_resolved_by_the_bridge_without_blocking(self) -> None:
        window = self._window()
        MainWindow._start_new_chat_for_phone(window, self.PHONE)
        window.xmpp.resolve_whatsapp_phone.assert_called_once()
        window._open_chat.assert_not_called()
        request_id = window._pending_phone_resolution[0]

        # A stale answer is ignored; the real one opens the JID WhatsApp uses.
        MainWindow._handle_whatsapp_phone_resolved(
            window, WhatsAppPhoneResolved("other", self.PHONE.e164, "found", jid=CHAT)
        )
        window._open_chat.assert_not_called()
        resolved = "+1000000000100@whatsapp.example.test"
        MainWindow._handle_whatsapp_phone_resolved(
            window, WhatsAppPhoneResolved(request_id, self.PHONE.e164, "found", jid=resolved)
        )
        chat = window._open_chat.call_args.args[0]
        self.assertEqual(chat.jid, resolved)

    def test_numbers_without_whatsapp_are_reported_and_not_opened(self) -> None:
        window = self._window()
        MainWindow._start_new_chat_for_phone(window, self.PHONE)
        request_id = window._pending_phone_resolution[0]
        with patch("cliente_xmpp.ui.main_window.wx.MessageBox") as message_box:
            MainWindow._handle_whatsapp_phone_resolved(
                window, WhatsAppPhoneResolved(request_id, self.PHONE.e164, "not_registered")
            )
        message_box.assert_called_once()
        window._open_chat.assert_not_called()
        window._open_chat_for_phone.assert_not_called()

    def test_unanswered_lookup_falls_back_to_the_local_jid(self) -> None:
        window = self._window()
        MainWindow._start_new_chat_for_phone(window, self.PHONE)
        request_id = window._pending_phone_resolution[0]
        MainWindow._handle_whatsapp_phone_resolved(
            window, WhatsAppPhoneResolved(request_id, self.PHONE.e164, "unavailable")
        )
        window._open_chat_for_phone.assert_called_once_with(self.PHONE)

    def test_known_chat_opens_immediately(self) -> None:
        window = self._window()
        window._chat_by_jid = lambda jid: Chat(jid=jid, name="Conocido") if jid == CHAT else None
        MainWindow._start_new_chat_for_phone(window, self.PHONE)
        window.xmpp.resolve_whatsapp_phone.assert_not_called()
        window._open_chat_for_phone.assert_called_once_with(self.PHONE)


if __name__ == "__main__":
    unittest.main()
