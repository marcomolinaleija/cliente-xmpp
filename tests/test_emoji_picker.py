from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx

from cliente_xmpp.config.settings import MAX_RECENT_EMOJIS, ConnectionSettings, SettingsStore
from cliente_xmpp.models.chat import Chat
from cliente_xmpp.models.emojis import emoji_catalog, matching_emojis
from cliente_xmpp.ui.conversation_panel import ConversationPanel
from cliente_xmpp.ui.emoji_picker_dialog import EMOJI_PAGE_SIZE, EmojiPickerDialog
from cliente_xmpp.ui.emoticons import EmoticonTextCtrl
from cliente_xmpp.ui.main_window import MainWindow


class EmojiCatalogTests(unittest.TestCase):
    def test_offline_catalog_preserves_sequences_variants_and_categories(self) -> None:
        with patch("urllib.request.urlopen", side_effect=AssertionError("Must stay offline")):
            entries, groups = emoji_catalog()
        values = [entry.value for entry in entries]
        self.assertGreater(len(values), 3000)
        self.assertEqual(len(values), len(set(values)))
        for value in ("❤️", "👍🏽", "👨‍👩‍👧‍👦", "🇲🇽"):
            with self.subTest(value=value):
                self.assertIn(value, values)
        self.assertTrue(all(entry.group in dict(groups) and entry.label for entry in entries))

    def test_search_uses_spanish_keywords_accents_multiple_words_and_aliases(self) -> None:
        entries, _ = emoji_catalog()
        for query, expected in (("CORAZÓN ROJO", "❤️"), ("corazon rojo", "❤️"),
                                ("risa", "😂"), ("perro", "🐶"), ("👍🏽", "👍🏽")):
            with self.subTest(query=query):
                self.assertIn(expected, [entry.value for entry in matching_emojis(query, entries)])
        self.assertEqual(matching_emojis("palabraficticiasincoincidencia", entries), [])

    def test_preferences_preserve_other_settings_and_bound_deduplicated_recents(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = SettingsStore(Path(folder) / "settings.json")
            connection = ConnectionSettings(jid="fixture@example.test", host="example.test")
            store.save_connection(connection)
            values = [entry.value for entry in emoji_catalog()[0]][:40]
            store.save_emoji_preferences(["❤️", "❤️", "👍🏽"], [values[0], *values])
            self.assertEqual(store.load_emoji_preferences(),
                             (["❤️", "👍🏽"], values[:MAX_RECENT_EMOJIS]))
            self.assertEqual(store.load_connection(), connection)
            for corrupt in (None, [], "invalid", {"favorites": [None, {}, "", "x" * 65],
                                                    "recent": "❤️"}):
                with self.subTest(corrupt=corrupt):
                    store.path.write_text(json.dumps({"emojis": corrupt}), encoding="utf-8")
                    self.assertEqual(store.load_emoji_preferences(), ([], []))


class NativeEmojiPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = wx.App.Get() or wx.App(False)

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = SettingsStore(Path(self.folder.name) / "settings.json")
        self.frame = wx.Frame(None)
        self.addCleanup(self.frame.Destroy)

    def dialog(self) -> EmojiPickerDialog:
        dialog = EmojiPickerDialog(self.frame, self.store)
        self.addCleanup(dialog.Destroy)
        return dialog

    def select(self, dialog, value):
        index = next(i for i, entry in enumerate(dialog._choices) if entry.value == value)
        dialog.results.SetSelection(index)
        dialog._on_selected(None)

    @staticmethod
    def key(code, *, control=False):
        return SimpleNamespace(GetKeyCode=lambda: code, ControlDown=lambda: control,
                               AltDown=lambda: False, Skip=Mock())

    def test_search_favorite_persistence_selection_empty_results_and_recent_order(self) -> None:
        dialog = self.dialog()
        dialog.search.SetValue("CORAZON ROJO")
        self.select(dialog, "❤️")
        dialog._on_favorite(None)
        self.assertEqual(dialog.selected_emoji(), "❤️")
        self.assertIn("favorito", dialog.results.GetString(dialog.results.GetSelection()))
        self.assertEqual(dialog.favorite_button.GetLabel(), "Quitar de favoritos")
        self.assertEqual(self.store.load_emoji_preferences(), (["❤️"], []))
        dialog.remember_selection("👍🏽")
        dialog.remember_selection("❤️")
        dialog.remember_selection("👍🏽")
        reopened = self.dialog()
        self.assertEqual(reopened.recent, ["👍🏽", "❤️"])
        reopened.section.SetSelection(2)
        reopened._on_filter_changed(None)
        self.assertEqual([entry.value for entry in reopened._choices], ["👍🏽", "❤️"])
        reopened.section.SetSelection(1)
        reopened._on_filter_changed(None)
        reopened._on_favorite(None)
        self.assertFalse(reopened.insert_button.IsEnabled())
        self.assertEqual(reopened.results.GetCount(), 0)
        self.assertEqual(self.store.load_emoji_preferences()[0], [])
        dialog.search.SetValue("palabraficticiasincoincidencia")
        self.assertEqual(dialog.selected_emoji(), "")
        self.assertFalse(dialog.favorite_button.IsEnabled())

    def test_enter_escape_arrows_and_favorite_shortcut(self) -> None:
        dialog = self.dialog()
        dialog.search.SetValue("corazon rojo")
        self.select(dialog, "❤️")
        with patch.object(dialog, "EndModal") as close:
            dialog._on_results_key(self.key(wx.WXK_RETURN))
            close.assert_called_once_with(wx.ID_OK)
            close.reset_mock()
            dialog._on_key(self.key(wx.WXK_ESCAPE))
            close.assert_called_once_with(wx.ID_CANCEL)
        self.assertEqual(self.store.load_emoji_preferences()[1], [])
        event = self.key(wx.WXK_DOWN)
        with patch.object(dialog.results, "SetFocus") as focus:
            dialog._on_search_key(event)
            focus.assert_called_once()
        event.Skip.assert_not_called()
        dialog._on_key(self.key(ord("D"), control=True))
        self.assertEqual(self.store.load_emoji_preferences()[0], ["❤️"])

    def test_first_page_searches_full_catalog_and_filters_reset_loaded_rows(self) -> None:
        dialog = self.dialog()
        self.assertEqual(dialog.results.GetCount(), EMOJI_PAGE_SIZE)
        self.assertGreater(len(dialog._choices), EMOJI_PAGE_SIZE)
        target = dialog._choices[-1]
        dialog.search.SetValue(target.value)
        self.assertIn(target.value, [entry.value for entry in dialog._choices])
        self.assertLessEqual(dialog.results.GetCount(), EMOJI_PAGE_SIZE)
        dialog.search.SetValue("")
        dialog._load_more()
        self.assertEqual(dialog.results.GetCount(), 2 * EMOJI_PAGE_SIZE)
        dialog.search.SetValue("corazon rojo")
        self.assertEqual(dialog.selected_emoji(), "❤️")
        self.assertFalse(dialog.load_more_button.IsShown())

    def test_bounded_append_crosses_two_pages_and_last_partial_without_rebuilding(self) -> None:
        entries, groups = emoji_catalog()
        fixture = entries[:2 * EMOJI_PAGE_SIZE + 7]
        with patch("cliente_xmpp.ui.emoji_picker_dialog.emoji_catalog",
                   return_value=(fixture, groups)):
            dialog = self.dialog()
        dialog.results.SetSelection(EMOJI_PAGE_SIZE - 1)
        dialog.results.SetFirstItem(EMOJI_PAGE_SIZE - 25)
        top = dialog.results.GetTopItem()
        selected = dialog.selected_emoji()
        event = self.key(wx.WXK_DOWN)
        with patch.object(dialog.results, "Set", wraps=dialog.results.Set) as rebuild:
            dialog._on_results_key(event)
            self.assertEqual(dialog.results.GetCount(), 2 * EMOJI_PAGE_SIZE)
            self.assertEqual(dialog.selected_emoji(), selected)
            self.assertEqual(dialog.results.GetTopItem(), top)
            event.Skip.assert_called_once()
            dialog._load_more()
            self.assertEqual(dialog.results.GetCount(), len(fixture))
            self.assertEqual(dialog.results.GetString(len(fixture) - 1),
                             dialog._row_label(fixture[-1]))
            self.assertFalse(dialog.load_more_button.IsShown())
            dialog._load_more()
            self.assertEqual(dialog.results.GetCount(), len(fixture))
            rebuild.assert_not_called()

    def test_favorite_changes_preserve_loaded_pages_and_selection(self) -> None:
        dialog = self.dialog()
        dialog._load_more()
        dialog.results.SetSelection(EMOJI_PAGE_SIZE + 5)
        selected = dialog.selected_emoji()
        with patch.object(dialog.results, "Set", wraps=dialog.results.Set) as rebuild:
            dialog._on_favorite(None)
            self.assertEqual(dialog.selected_emoji(), selected)
            self.assertEqual(dialog.results.GetCount(), 2 * EMOJI_PAGE_SIZE)
            dialog._on_favorite(None)
            self.assertEqual(dialog.selected_emoji(), selected)
            rebuild.assert_not_called()
        favorites = [entry.value for entry in dialog._catalog[:2 * EMOJI_PAGE_SIZE + 7]]
        self.store.save_emoji_preferences(favorites, [])
        reopened = self.dialog()
        reopened.section.SetSelection(1)
        reopened._on_filter_changed(None)
        reopened._load_more()
        reopened.results.SetSelection(EMOJI_PAGE_SIZE + 5)
        index = reopened.results.GetSelection()
        removed = reopened.selected_emoji()
        following = reopened._choices[index + 1].value
        reopened._on_favorite(None)
        self.assertNotIn(removed, reopened.favorites)
        self.assertEqual(reopened.selected_emoji(), following)
        self.assertEqual(reopened.results.GetCount(), 2 * EMOJI_PAGE_SIZE)

    def test_category_filter_unknown_saved_values_and_storage_failure(self) -> None:
        self.store.save_emoji_preferences(["not-an-emoji", "❤️"], ["unknown", "🐶"])
        dialog = self.dialog()
        self.assertEqual(dialog.favorites, ["❤️"])
        self.assertEqual(dialog.recent, ["🐶"])
        dialog.section.SetSelection(dialog._sections.index(3))
        dialog._on_filter_changed(None)
        self.assertTrue(dialog._choices)
        self.assertTrue(all(entry.group == 3 for entry in dialog._choices))
        self.select(dialog, "🐶")
        with patch.object(self.store, "save_emoji_preferences", side_effect=OSError), \
                patch("cliente_xmpp.ui.emoji_picker_dialog.wx.MessageBox") as error:
            dialog._on_favorite(None)
            error.assert_called_once()
        self.assertEqual(dialog.favorites, ["❤️"])

    def test_button_follows_editor_before_recording_in_layout_and_tab_order(self) -> None:
        with patch("cliente_xmpp.ui.conversation_panel.MpvAudioPlayer"), \
                patch("cliente_xmpp.ui.conversation_panel.NvdaSpeaker"):
            panel = ConversationPanel(self.frame, lambda jid: jid)
        panel.set_chat(Chat("room@example.test", "Fixture"))
        children = [item.GetWindow() for item in panel.compose.GetContainingSizer().GetChildren()]
        self.assertEqual(children[:3], [panel.compose, panel.emoji_button, panel.send_button])
        self.assertEqual(panel.compose.GetNextSibling(), panel.emoji_button)
        self.assertEqual(panel.emoji_button.GetNextSibling(), panel.send_button)
        panel.set_remote_actions_enabled(True)
        panel.set_recording_state(False)
        self.assertTrue(panel.emoji_button.IsEnabled())
        self.assertIn("Grabar audio", panel.send_button.GetLabel())
        panel.set_recording_state(True)
        self.assertFalse(panel.emoji_button.IsEnabled())
        panel.set_recording_state(False)
        panel.set_remote_actions_enabled(False)
        self.assertFalse(panel.emoji_button.IsEnabled())

    def window_with_composer(self):
        compose = EmoticonTextCtrl(self.frame, style=wx.TE_MULTILINE)
        compose.ChangeValue("👋\nHola selección final")
        end = compose.GetLastPosition() - len(" final")
        compose.SetSelection(end - len("selección"), end)
        conversation = SimpleNamespace(compose=compose, current_chat=Chat("a@test", "Fixture"))
        return SimpleNamespace(conversation=conversation, settings_store=self.store,
                               status_bar=SimpleNamespace(SetStatusText=Mock()))

    def test_insertion_replaces_native_selection_is_undoable_and_never_sends(self) -> None:
        window = self.window_with_composer()
        window.xmpp = SimpleNamespace(send_message=Mock(), send_reply=Mock())
        changes = []
        window.conversation.compose.Bind(wx.EVT_TEXT, lambda _: changes.append(
            window.conversation.compose.GetValue()))
        with patch("cliente_xmpp.ui.main_window.EmojiPickerDialog") as factory:
            dialog = factory.return_value
            dialog.ShowModal.return_value = wx.ID_OK
            dialog.selected_emoji.return_value = "👨‍👩‍👧‍👦"
            MainWindow._on_open_emoji_picker(window, None)
            dialog.remember_selection.assert_called_once_with("👨‍👩‍👧‍👦")
            dialog.Destroy.assert_called_once()
        compose = window.conversation.compose
        self.assertEqual(compose.GetValue(), "👋\nHola 👨‍👩‍👧‍👦 final")
        self.assertEqual(compose.GetRange(0, compose.GetInsertionPoint()), "👋\nHola 👨‍👩‍👧‍👦")
        self.assertEqual(changes, [compose.GetValue()])
        window.xmpp.send_message.assert_not_called()
        window.xmpp.send_reply.assert_not_called()
        compose.Undo()
        self.assertEqual(compose.GetValue(), "👋\nHola selección final")

    def test_cancel_and_concurrent_draft_or_chat_change_never_overwrite_text(self) -> None:
        for action in ("cancel", "draft", "chat", "disabled"):
            with self.subTest(action=action):
                window = self.window_with_composer()
                compose = window.conversation.compose
                original = compose.GetValue()
                selection = compose.GetSelection()
                def modal(action=action, compose=compose, window=window):
                    if action == "draft":
                        compose.ChangeValue("Otro borrador ficticio")
                    elif action == "chat":
                        window.conversation.current_chat = Chat("b@test", "Other fixture")
                    elif action == "disabled":
                        compose.Enable(False)
                    return wx.ID_CANCEL if action == "cancel" else wx.ID_OK
                with patch("cliente_xmpp.ui.main_window.EmojiPickerDialog") as factory:
                    factory.return_value.ShowModal.side_effect = modal
                    MainWindow._on_open_emoji_picker(window, None)
                    factory.return_value.remember_selection.assert_not_called()
                self.assertEqual(compose.GetValue(),
                                 "Otro borrador ficticio" if action == "draft" else original)
                if action == "cancel":
                    self.assertEqual(compose.GetSelection(), selection)
