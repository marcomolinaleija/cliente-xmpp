from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

import wx

from cliente_xmpp.config.settings import SettingsStore
from cliente_xmpp.models.transcription import (
    AUTO_ALL,
    AUTO_ALL_EXCEPT,
    AUTO_OFF,
    AUTO_SELECTED,
    DELIVERY_PRIVATE,
    DELIVERY_REPLY,
    TRANSCRIPTION_NS,
    TranscriptionSettings,
    TranscriptionSettingsUpdate,
    build_transcription_settings_update,
    parse_transcription_settings,
    transcription_error_text,
    validate_api_key,
)

CHAT = "+10000000001@whatsapp.example.test"
GROUP = "#120000000000000001@whatsapp.example.test"


def _settings_xml(**attrs: str) -> ET.Element:
    node = ET.Element(f"{{{TRANSCRIPTION_NS}}}settings", attrs)
    return node


class TranscriptionModelTests(unittest.TestCase):
    def test_bridge_state_is_projected_onto_one_automatic_mode(self) -> None:
        cases = (
            (True, {}, AUTO_ALL, frozenset()),
            (True, {GROUP: False, CHAT: True}, AUTO_ALL_EXCEPT, frozenset({GROUP})),
            (False, {}, AUTO_OFF, frozenset()),
            (False, {CHAT: True, GROUP: False}, AUTO_SELECTED, frozenset({CHAT})),
        )
        for auto, overrides, mode, selected in cases:
            with self.subTest(auto=auto, overrides=overrides):
                settings = TranscriptionSettings(auto_enabled=auto, chat_overrides=overrides)
                self.assertEqual(settings.auto_mode, mode)
                self.assertEqual(settings.selected_chats, selected)

    def test_parse_reads_state_without_ever_expecting_the_key(self) -> None:
        node = _settings_xml(
            available="1", service="1", auto="0", delivery="reply",
            **{"key-source": "account", "key-hint": "abcd"},
        )
        ET.SubElement(node, f"{{{TRANSCRIPTION_NS}}}chat", {"jid": CHAT.upper(), "enabled": "1"})
        iq = ET.Element("{jabber:client}iq")
        iq.append(node)

        settings = parse_transcription_settings(iq)

        self.assertTrue(settings.available)
        self.assertEqual(settings.delivery, DELIVERY_REPLY)
        self.assertEqual(settings.key_hint, "abcd")
        self.assertEqual(settings.chat_overrides, {CHAT.casefold(): True})
        self.assertEqual(settings.auto_mode, AUTO_SELECTED)

    def test_unknown_values_fall_back_to_safe_defaults(self) -> None:
        settings = parse_transcription_settings(
            _settings_xml(delivery="everyone", **{"key-source": "stolen"})
        )
        self.assertEqual(settings.delivery, DELIVERY_PRIVATE)
        self.assertEqual(settings.key_source, "none")
        with self.assertRaises(ValueError):
            parse_transcription_settings(ET.Element("{jabber:client}iq"))

    def test_update_xml_carries_mode_exceptions_and_key_actions(self) -> None:
        update = TranscriptionSettingsUpdate(
            service_enabled=True, auto_mode=AUTO_ALL_EXCEPT, delivery=DELIVERY_PRIVATE,
            chats=frozenset({GROUP}), api_key="dg-0123456789abcdef",
        )
        node = build_transcription_settings_update(update)
        self.assertEqual((node.get("service"), node.get("auto")), ("1", "1"))
        key = node.find(f"{{{TRANSCRIPTION_NS}}}api-key")
        self.assertEqual(key.text, "dg-0123456789abcdef")
        chats = node.findall(f"{{{TRANSCRIPTION_NS}}}chats/{{{TRANSCRIPTION_NS}}}chat")
        self.assertEqual([(c.get("jid"), c.get("enabled")) for c in chats], [(GROUP, "0")])

        cleared = build_transcription_settings_update(TranscriptionSettingsUpdate(
            service_enabled=False, auto_mode=AUTO_OFF, delivery=DELIVERY_REPLY,
            api_key="ignored-because-clear", clear_api_key=True,
        ))
        key = cleared.find(f"{{{TRANSCRIPTION_NS}}}api-key")
        self.assertEqual(key.get("clear"), "1")
        self.assertIsNone(key.text)
        self.assertEqual(cleared.findall(f".//{{{TRANSCRIPTION_NS}}}chat"), [])

    def test_key_validation_and_error_messages(self) -> None:
        self.assertEqual(validate_api_key("  abc123  "), "abc123")
        for bad in ("", "   ", "con espacio", "x" * 300, "tab\tkey"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_api_key(bad)
        self.assertIn("crédito", transcription_error_text("payment-required"))
        self.assertIn("clave", transcription_error_text("not-authorized"))
        self.assertIn(
            "Audio demasiado", transcription_error_text("not-acceptable", "Audio demasiado")
        )
        self.assertTrue(transcription_error_text("unexpected-condition"))

    def test_emoticon_conversion_defaults_on_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            self.assertTrue(store.load_emoticon_conversion())
            store.save_emoticon_conversion(False)
            self.assertFalse(SettingsStore(store.path).load_emoticon_conversion())


class PreferencesDialogNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = wx.App.Get() or wx.App(False)

    def setUp(self) -> None:
        from cliente_xmpp.ui.settings_panel import PAGE_TITLES, PreferencesDialog

        self.frame = wx.Frame(None)
        self.dialog = PreferencesDialog(self.frame)
        self.page_titles = PAGE_TITLES

    def tearDown(self) -> None:
        self.dialog.Destroy()
        self.frame.Destroy()

    def test_tabs_and_standard_buttons_exist(self) -> None:
        notebook = self.dialog.notebook
        titles = [notebook.GetPageText(i) for i in range(notebook.GetPageCount())]
        self.assertEqual(titles, list(self.page_titles))
        self.assertEqual(self.dialog.save_button.GetId(), wx.ID_OK)
        self.assertEqual(self.dialog.cancel_button.GetId(), wx.ID_CANCEL)
        self.assertEqual(self.dialog.apply_button.GetId(), wx.ID_APPLY)
        self.assertEqual(self.dialog.GetEscapeId(), wx.ID_CANCEL)
        # Every focusable field of the transcription tab has an accessible name.
        for control in (
            self.dialog.api_key, self.dialog.transcription_auto_mode,
            self.dialog.transcription_chats, self.dialog.transcription_chat_filter,
        ):
            self.assertTrue(control.GetName().strip())

    def test_apply_runs_handler_and_save_keeps_dialog_open_on_failure(self) -> None:
        calls: list[str] = []
        self.dialog.apply_handler = lambda: calls.append("apply") or False
        self.dialog._on_apply(wx.CommandEvent())
        self.dialog._on_save(wx.CommandEvent())
        self.assertEqual(calls, ["apply", "apply"])

    def test_transcription_controls_wait_for_the_bridge(self) -> None:
        self.dialog.set_transcription_loading()
        self.assertFalse(self.dialog.transcription_service.IsEnabled())
        self.assertIsNone(self.dialog.transcription_update())
        self.dialog.set_transcription_unavailable("Este puente no permite configurarla.")
        self.assertIn("no permite", self.dialog.transcription_status.GetLabel())

    def test_unchanged_settings_send_nothing_and_changes_build_one_update(self) -> None:
        settings = TranscriptionSettings(
            available=True, auto_enabled=False, key_source="account", key_hint="abcd",
            chat_overrides={CHAT: True},
        )
        chats = [(CHAT, "Contacto ficticio (chat)"), (GROUP, "Grupo ficticio (grupo)")]
        self.dialog.set_transcription_settings(settings, chats)

        self.assertIsNone(self.dialog.transcription_update())
        self.assertTrue(self.dialog.transcription_chats.IsEnabled())
        self.assertIn("abcd", self.dialog.transcription_status.GetLabel())

        # Filtering keeps the hidden selection.
        self.dialog.transcription_chat_filter.SetValue("grupo")
        self.dialog._fill_chat_list()
        self.assertEqual(self.dialog.transcription_chats.GetCount(), 1)
        self.dialog.transcription_chats.Check(0, True)
        event = wx.CommandEvent(wx.wxEVT_CHECKLISTBOX)
        event.SetInt(0)
        self.dialog._on_chat_checked(event)
        self.dialog.transcription_delivery.SetSelection(1)

        update = self.dialog.transcription_update()
        self.assertIsNotNone(update)
        self.assertEqual(update.auto_mode, AUTO_SELECTED)
        self.assertEqual(update.chats, frozenset({CHAT, GROUP}))
        self.assertEqual(update.delivery, DELIVERY_REPLY)
        self.assertIsNone(update.api_key)

    def test_invalid_key_is_reported_before_anything_is_sent(self) -> None:
        self.dialog.set_transcription_settings(TranscriptionSettings(available=True), [])
        self.dialog.api_key.SetValue("clave con espacios")
        with self.assertRaises(ValueError):
            self.dialog.transcription_update()

    def test_emoticon_checkbox_round_trips_through_set_values(self) -> None:
        self.dialog.set_values(
            windows_notifications=False, show_preview=True, announce_with_nvda=False,
            open_chat_sound=True, sent_message_sound=True, incoming_sound_path="",
            minimize_to_tray_on_alt_f4=False, emoticon_conversion=False,
        )
        self.assertFalse(self.dialog.emoticon_conversion.GetValue())
        # Notification details depend on the main notification switch.
        self.assertFalse(self.dialog.show_preview.IsEnabled())


if __name__ == "__main__":
    unittest.main()
