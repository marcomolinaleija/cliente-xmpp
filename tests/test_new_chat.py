from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.models.phone_numbers import (
    PhoneNumberError,
    country_dialing_options,
    normalize_phone_number,
    whatsapp_contact_jid,
    whatsapp_contact_jid_candidates,
)
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.ui.main_window import MainWindow


class PhoneNumberTests(unittest.TestCase):
    def test_country_catalog_contains_all_supported_regions(self) -> None:
        options = country_dialing_options()
        regions = {option.region_code for option in options}

        self.assertGreaterEqual(len(options), 240)
        self.assertIn("MX", regions)
        self.assertIn("GB", regions)
        self.assertIn("XK", regions)
        self.assertTrue(all(option.country_name for option in options))

    def test_mexican_national_number_is_normalized(self) -> None:
        normalized = normalize_phone_number("449 123 4567", "MX")

        self.assertEqual(normalized.e164, "+524491234567")
        self.assertIn("52", normalized.international)

    def test_legacy_mexican_whatsapp_number_is_preserved(self) -> None:
        normalized = normalize_phone_number("+521 449 386 0911", "MX")

        self.assertEqual(normalized.e164, "+5214493860911")
        self.assertEqual(normalized.international, "+52 1 449 386 0911")

    def test_legacy_mexican_whatsapp_number_accepts_international_prefix(self) -> None:
        with_double_zero = normalize_phone_number("00521 449 386 0911", "GB")
        without_plus = normalize_phone_number("5214493860911", "MX")

        self.assertEqual(with_double_zero.e164, "+5214493860911")
        self.assertEqual(without_plus.e164, "+5214493860911")

    def test_selected_country_removes_national_trunk_prefix(self) -> None:
        normalized = normalize_phone_number("020 7946 0018", "GB")

        self.assertEqual(normalized.e164, "+442079460018")

    def test_complete_international_number_overrides_selected_country(self) -> None:
        with_plus = normalize_phone_number("+44 20 7946 0018", "MX")
        with_double_zero = normalize_phone_number("0044 20 7946 0018", "MX")

        self.assertEqual(with_plus.e164, "+442079460018")
        self.assertEqual(with_double_zero.e164, with_plus.e164)

    def test_number_with_country_code_but_without_plus_is_accepted(self) -> None:
        normalized = normalize_phone_number("524491234567", "MX")

        self.assertEqual(normalized.e164, "+524491234567")

    def test_invalid_characters_are_rejected(self) -> None:
        with self.assertRaises(PhoneNumberError):
            normalize_phone_number("449 123 4567 ext 2", "MX")

    def test_impossible_number_is_rejected(self) -> None:
        with self.assertRaises(PhoneNumberError):
            normalize_phone_number("123", "MX")

    def test_contact_jid_uses_the_active_whatsapp_component(self) -> None:
        self.assertEqual(
            whatsapp_contact_jid("+524491234567", "whatsapp.example.org"),
            "+524491234567@whatsapp.example.org",
        )

    def test_mexican_whatsapp_jid_candidates_include_known_legacy_alias(self) -> None:
        self.assertEqual(
            whatsapp_contact_jid_candidates(
                "+524493860911",
                "whatsapp.example.org",
            ),
            (
                "+524493860911@whatsapp.example.org",
                "+5214493860911@whatsapp.example.org",
            ),
        )

    def test_legacy_mexican_jid_candidates_include_modern_alias(self) -> None:
        self.assertEqual(
            whatsapp_contact_jid_candidates(
                "+5214493860911",
                "whatsapp.example.org",
            ),
            (
                "+5214493860911@whatsapp.example.org",
                "+524493860911@whatsapp.example.org",
            ),
        )

    def test_other_country_has_only_one_whatsapp_jid_candidate(self) -> None:
        self.assertEqual(
            whatsapp_contact_jid_candidates(
                "+442079460018",
                "whatsapp.example.org",
            ),
            ("+442079460018@whatsapp.example.org",),
        )


class DirectChatMaterializationTests(unittest.TestCase):
    def test_temporary_chat_is_materialized_when_first_message_is_added(self) -> None:
        chat = Chat(
            jid="+524491234567@whatsapp.example.org",
            name="+52 449 123 4567",
        )
        message = Message(
            chat_jid=chat.jid,
            sender_jid="me",
            body="Hola",
            outgoing=True,
        )
        window = MainWindow.__new__(MainWindow)
        window.conversation = SimpleNamespace(current_chat=chat)
        window.searchable_chats_by_jid = {}
        window.chat_names_by_jid = {}
        window.chat_list = SimpleNamespace(
            has_chat=Mock(return_value=False),
            upsert_chat=Mock(),
        )
        window._chat_by_jid = Mock(return_value=None)

        MainWindow._ensure_chat_for_message(window, message)

        self.assertIs(window.searchable_chats_by_jid[chat.jid], chat)
        self.assertEqual(window.chat_names_by_jid[chat.jid], chat.name)
        window.chat_list.upsert_chat.assert_called_once_with(chat)


class GroupPrivateMessageTests(unittest.TestCase):
    class _MenuItem:
        def __init__(self, label: str) -> None:
            self.label = label
            self.enabled = True

        def Enable(self, enabled: bool) -> None:
            self.enabled = enabled

    class _Menu:
        def __init__(self) -> None:
            self.items: list[GroupPrivateMessageTests._MenuItem] = []

        def Append(self, _item_id: int, label: str) -> GroupPrivateMessageTests._MenuItem:
            item = GroupPrivateMessageTests._MenuItem(label)
            self.items.append(item)
            return item

        def AppendSubMenu(self, _submenu: object, label: str) -> None:
            self.items.append(GroupPrivateMessageTests._MenuItem(label))

        def Destroy(self) -> None:
            return

    @staticmethod
    def _window(group: Chat) -> MainWindow:
        window = MainWindow.__new__(MainWindow)
        window.conversation = SimpleNamespace(current_chat=group)
        window.whatsapp_component_jid = ""
        window.group_participants_by_chat = {}
        return window

    @staticmethod
    def _group_message(group: Chat, sender_jid: str, *, outgoing: bool = False) -> Message:
        return Message(
            chat_jid=group.jid,
            sender_jid=sender_jid,
            body="Hola",
            outgoing=outgoing,
            chat_is_group=True,
        )

    def test_group_sender_phone_is_available_for_private_message(self) -> None:
        group = Chat(jid="#room@whatsapp.example.org", name="Grupo", is_group=True)
        window = self._window(group)
        message = self._group_message(
            group,
            "+524491234567@whatsapp.example.org",
        )

        recipient = MainWindow._private_message_recipient(window, message)

        self.assertIsNotNone(recipient)
        normalized, component_jid = recipient
        self.assertEqual(normalized.e164, "+524491234567")
        self.assertEqual(component_jid, "whatsapp.example.org")


    def test_context_menu_appends_and_binds_private_message_action(self) -> None:
        group = Chat(jid="#room@whatsapp.example.org", name="Grupo", is_group=True)
        window = self._window(group)
        message = self._group_message(
            group,
            "+524491234567@whatsapp.example.org",
        )
        window.conversation = SimpleNamespace(
            current_chat=None,
            selected_message=Mock(return_value=message),
        )
        window.messages_by_chat = {group.jid: [message]}
        window._message_can_be_edited = Mock(return_value=False)
        window._send_private_message_to_group_sender = Mock()
        window._reply_privately_to_group_message = Mock()

        with (
            patch("cliente_xmpp.ui.main_window.wx.Menu", self._Menu),
            patch.object(MainWindow, "Bind") as bind,
            patch.object(MainWindow, "PopupMenu") as popup_menu,
        ):
            MainWindow._show_message_context_menu(window)

        shown_menu = popup_menu.call_args.args[0]
        private_item = next(
            item for item in shown_menu.items if item.label == "Enviar mensaje privado"
        )
        private_reply_item = next(
            item for item in shown_menu.items if item.label == "Responder en privado"
        )
        private_binding = next(
            call for call in bind.call_args_list if call.args[2] is private_item
        )
        private_binding.args[1](None)
        private_reply_binding = next(
            call for call in bind.call_args_list if call.args[2] is private_reply_item
        )
        private_reply_binding.args[1](None)

        window._send_private_message_to_group_sender.assert_called_once()
        normalized, component_jid = (
            window._send_private_message_to_group_sender.call_args.args
        )
        self.assertEqual(normalized.e164, "+524491234567")
        self.assertEqual(component_jid, "whatsapp.example.org")
        window._reply_privately_to_group_message.assert_called_once()
        reply_message, reply_phone, reply_component = (
            window._reply_privately_to_group_message.call_args.args
        )
        self.assertIs(reply_message, message)
        self.assertEqual(reply_phone.e164, "+524491234567")
        self.assertEqual(reply_component, "whatsapp.example.org")

    def test_context_menu_uses_popup_parent_for_bindings_and_display(self) -> None:
        group = Chat(jid="#room@whatsapp.example.org", name="Grupo", is_group=True)
        window = self._window(group)
        message = self._group_message(
            group,
            "+524491234567@whatsapp.example.org",
        )
        window.conversation = SimpleNamespace(
            current_chat=None,
            selected_message=Mock(return_value=message),
        )
        window.messages_by_chat = {group.jid: [message]}
        window._message_can_be_edited = Mock(return_value=False)

        class PopupOwner:
            def __init__(self) -> None:
                self.bind_calls: list[tuple[object, object, object]] = []
                self.popup_menus: list[object] = []

            def Bind(self, event_type: object, handler: object, item: object) -> None:
                self.bind_calls.append((event_type, handler, item))

            def PopupMenu(self, menu: object) -> None:
                self.popup_menus.append(menu)

        popup_parent = PopupOwner()

        with (
            patch("cliente_xmpp.ui.main_window.wx.Menu", self._Menu),
            patch.object(MainWindow, "Bind") as main_window_bind,
            patch.object(MainWindow, "PopupMenu") as main_window_popup,
        ):
            MainWindow._show_message_context_menu(
                window,
                message,
                popup_parent=popup_parent,  # type: ignore[arg-type]
            )

        self.assertEqual(len(popup_parent.popup_menus), 1)
        self.assertEqual(len(popup_parent.bind_calls), 13)
        self.assertTrue(
            all(
                event_type is wx.EVT_MENU
                for event_type, _handler, _item in popup_parent.bind_calls
            )
        )
        self.assertEqual(
            len({id(item) for _event, _handler, item in popup_parent.bind_calls}),
            13,
        )
        main_window_bind.assert_not_called()
        main_window_popup.assert_not_called()

    def test_browser_context_menu_uses_local_path_for_downloaded_media_text_copy(self) -> None:
        group = Chat(jid="#room@whatsapp.example.org", name="Grupo", is_group=True)
        window = self._window(group)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "document.pdf"
            path.write_bytes(b"document")
            message = Message(
                chat_jid=group.jid,
                sender_jid="sender@whatsapp.example.org",
                body="Archivo",
                media_url="https://example.org/document.pdf",
                media_kind="file",
                media_local_path=str(path),
            )
            window._browser_message_in_memory = Mock(return_value=message)
            window._show_message_context_menu = Mock()
            popup_parent = object()

            MainWindow._show_browser_message_context_menu(
                window,
                message,
                popup_parent,  # type: ignore[arg-type]
            )

            window._show_message_context_menu.assert_called_once_with(
                message,
                popup_parent=popup_parent,
                copy_text_path=True,
            )

    def test_private_reply_keeps_the_group_quote_and_opens_private_chat(self) -> None:
        group = Chat(jid="#room@whatsapp.example.org", name="Grupo", is_group=True)
        private_chat = Chat(
            jid="+524491234567@whatsapp.example.org",
            name="Rabanita",
        )
        message = Message(
            chat_jid=group.jid,
            sender_jid="+524491234567@whatsapp.example.org",
            sender_name="Rabanita",
            body="Mensaje del grupo",
            message_id="group-message-id",
            chat_is_group=True,
        )
        window = self._window(group)
        window.current_jid = "me@example.test"
        window.reply_context = None
        window.edit_context = None
        window.status_bar = SimpleNamespace(SetStatusText=Mock())
        window._require_whatsapp_connection = lambda: True
        window._open_chat_for_phone = Mock(return_value=private_chat)
        window.conversation = SimpleNamespace(
            current_chat=group,
            clear_editing=Mock(),
            insert_reply_quote=Mock(),
        )

        MainWindow._reply_privately_to_group_message(
            window,
            message,
            normalize_phone_number("+52 449 123 4567"),
            "whatsapp.example.org",
        )

        window._open_chat_for_phone.assert_called_once()
        self.assertEqual(window.reply_context.chat_jid, private_chat.jid)
        self.assertEqual(window.reply_context.sender_jid, f"{group.jid}/Rabanita")
        self.assertFalse(window.reply_context.chat_is_group)
        self.assertEqual(window.reply_context.message_id, message.message_id)
        window.conversation.insert_reply_quote.assert_called_once_with(message)
        window.status_bar.SetStatusText.assert_called_once_with("Respuesta privada preparada")

    def test_group_sender_loaded_from_sqlite_is_available_for_private_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            account_jid = "me@example.test"
            group = Chat(
                jid="#room@whatsapp.example.org",
                name="Grupo",
                is_group=True,
            )
            stored_message = Message(
                chat_jid=group.jid,
                sender_jid="+524491234567@whatsapp.example.org",
                sender_name="Participante",
                body="Mensaje",
                sent_at=datetime(2026, 7, 24, 12, tzinfo=UTC),
                outgoing=False,
                message_id="group-message",
                chat_is_group=True,
            )
            store.upsert_chat(account_jid, group)
            store.upsert_messages(account_jid, [stored_message])

            loaded_group = store.load_chats(account_jid)[0]
            loaded_message = store.load_recent_messages(
                account_jid,
                group.jid,
            )[0]

        window = self._window(loaded_group)
        recipient = MainWindow._private_message_recipient(window, loaded_message)

        self.assertTrue(loaded_group.is_group)
        self.assertTrue(loaded_message.chat_is_group)
        self.assertIsNotNone(recipient)
        normalized, component_jid = recipient
        self.assertEqual(normalized.e164, "+524491234567")
        self.assertEqual(component_jid, "whatsapp.example.org")

    def test_private_message_is_hidden_without_valid_group_identity(self) -> None:
        group = Chat(jid="#room@whatsapp.example.org", name="Grupo", is_group=True)
        window = self._window(group)
        invalid_messages = (
            self._group_message(group, "#room@whatsapp.example.org/Nickname"),
            self._group_message(group, "nickname@whatsapp.example.org"),
            self._group_message(group, "+524491234567@other.example.org"),
            self._group_message(
                group,
                "+524491234567@whatsapp.example.org",
                outgoing=True,
            ),
        )

        for message in invalid_messages:
            with self.subTest(sender_jid=message.sender_jid, outgoing=message.outgoing):
                self.assertIsNone(
                    MainWindow._private_message_recipient(window, message)
                )

    def test_muc_occupant_is_hidden_even_when_participant_cache_has_a_match(self) -> None:
        group = Chat(jid="#room@whatsapp.example.org", name="Grupo", is_group=True)
        window = self._window(group)
        window.group_participants_by_chat[group.jid] = {
            "+524491234567@whatsapp.example.org": SimpleNamespace(
                jid="+524491234567@whatsapp.example.org",
                nick="Nickname",
            )
        }
        message = self._group_message(
            group,
            "#room@whatsapp.example.org/Nickname",
        )
        message.sender_name = "Nickname"

        self.assertIsNone(MainWindow._private_message_recipient(window, message))

    def test_private_message_reuses_existing_new_chat_resolution(self) -> None:
        group = Chat(jid="#room@whatsapp.example.org", name="Grupo", is_group=True)
        existing = Chat(
            jid="+5214493860911@whatsapp.example.org",
            name="Contacto",
        )
        window = self._window(group)
        window._chat_by_jid = Mock(
            side_effect=lambda jid: existing if jid == existing.jid else None
        )
        window._open_chat = Mock()

        MainWindow._open_chat_for_phone(
            window,
            normalize_phone_number("+52 449 386 0911"),
            "whatsapp.example.org",
        )

        window._open_chat.assert_called_once_with(existing)

    def test_private_message_creates_same_temporary_chat_as_new_chat(self) -> None:
        group = Chat(jid="#room@whatsapp.example.org", name="Grupo", is_group=True)
        window = self._window(group)
        window._chat_by_jid = Mock(return_value=None)
        window._open_chat = Mock()
        normalized = normalize_phone_number("+44 20 7946 0018")

        MainWindow._open_chat_for_phone(
            window,
            normalized,
            "whatsapp.example.org",
        )

        temporary_chat = window._open_chat.call_args.args[0]
        self.assertEqual(
            temporary_chat.jid,
            "+442079460018@whatsapp.example.org",
        )
        self.assertEqual(temporary_chat.name, normalized.international)
        self.assertFalse(window._open_chat.call_args.kwargs["request_remote_context"])


class ChatContextMenuTests(unittest.TestCase):
    class _MenuItem:
        def __init__(self, label: str) -> None:
            self.label = label
            self.enabled = True

        def Enable(self, enabled: bool) -> None:
            self.enabled = enabled

    class _Menu:
        def __init__(self) -> None:
            self.items: list[ChatContextMenuTests._MenuItem] = []

        def Append(self, _item_id: int, label: str) -> ChatContextMenuTests._MenuItem:
            item = ChatContextMenuTests._MenuItem(label)
            self.items.append(item)
            return item

        def AppendSeparator(self) -> None:
            return

        def Destroy(self) -> None:
            return

    def test_menu_uses_dynamic_mute_and_pin_labels_and_respects_pin_limit(self) -> None:
        chat = Chat(
            jid="chat@example.test",
            name="Chat",
            notifications_muted=True,
        )
        window = MainWindow.__new__(MainWindow)
        window.chat_list = SimpleNamespace(selected_chat=Mock(return_value=chat))
        window.pinned_chat_jids = [f"pinned-{index}@example.test" for index in range(4)]

        with (
            patch("cliente_xmpp.ui.main_window.wx.Menu", self._Menu),
            patch.object(MainWindow, "Bind"),
            patch.object(MainWindow, "PopupMenu") as popup_menu,
        ):
            MainWindow._show_chat_context_menu(window)

        menu = popup_menu.call_args.args[0]
        items = {item.label: item for item in menu.items}
        self.assertIn("Cambiar nombre\tF2", items)
        self.assertIn("Desilenciar chat", items)
        self.assertIn("Fijar chat", items)
        self.assertFalse(items["Fijar chat"].enabled)
        self.assertIn("Eliminar chat...", items)


class NewChatShortcutTests(unittest.TestCase):
    @staticmethod
    def _event(*, control: bool, alt: bool = False, shift: bool = False) -> SimpleNamespace:
        return SimpleNamespace(
            ControlDown=lambda: control,
            AltDown=lambda: alt,
            ShiftDown=lambda: shift,
            GetKeyCode=lambda: ord("N"),
            GetUnicodeKey=lambda: ord("N"),
        )

    def test_control_n_opens_new_chat(self) -> None:
        self.assertTrue(MainWindow._is_new_chat_shortcut(self._event(control=True)))

    def test_modified_control_n_is_not_used(self) -> None:
        self.assertFalse(
            MainWindow._is_new_chat_shortcut(self._event(control=True, shift=True))
        )
        self.assertFalse(
            MainWindow._is_new_chat_shortcut(self._event(control=True, alt=True))
        )


if __name__ == "__main__":
    unittest.main()
