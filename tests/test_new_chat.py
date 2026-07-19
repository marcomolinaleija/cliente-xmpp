from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.models.phone_numbers import (
    PhoneNumberError,
    country_dialing_options,
    normalize_phone_number,
    whatsapp_contact_jid,
    whatsapp_contact_jid_candidates,
)
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
