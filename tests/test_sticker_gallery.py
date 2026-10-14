from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx
from PIL import Image

from cliente_xmpp.config.settings import SettingsStore
from cliente_xmpp.storage.sticker_library import StickerLibrary
from cliente_xmpp.ui.sticker_gallery_dialog import (
    StickerDescriptionDialog,
    StickerGalleryDialog,
    StickerGallerySettingsDialog,
)


class StickerGalleryTests(unittest.TestCase):
    """Real native wx controls, hidden, with isolated storage and mocked AI/network."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = wx.App.Get() or wx.App(False)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "fixture.png"
        Image.new("RGBA", (328, 300), "red").save(self.source)
        self.library = StickerLibrary(self.root / "library")
        self.settings = SettingsStore(self.root / "settings.json")
        self.frame = wx.Frame(None)
        self.dialog = StickerGalleryDialog(self.frame, self.library, self.settings, can_send=True)
        self.wait(lambda: self.library.path.exists() and not self.dialog._busy)

    def tearDown(self) -> None:
        self.dialog._shutdown()
        self.dialog._executor.shutdown(wait=True, cancel_futures=True)
        self.dialog.Destroy()
        self.frame.Destroy()
        self.app.Yield()
        self.temp.cleanup()

    def wait(self, condition) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.Yield()
            if condition():
                return
            time.sleep(0.01)
        self.fail("The bounded GUI worker did not finish")

    def choice(self, index: int):
        return SimpleNamespace(
            ShowModal=Mock(return_value=wx.ID_OK),
            Destroy=Mock(),
            choice=SimpleNamespace(GetSelection=lambda: index),
            always=SimpleNamespace(GetValue=lambda: False),
        )

    def batch_entries(self):
        entries = []
        for index, color in enumerate(("red", "blue", "green", "yellow")):
            path = self.root / f"batch-{index}.png"
            Image.new("RGBA", (32, 32), color).save(path)
            entries.append(self.library.add(path, name=f"Fixture {index}"))
        self.dialog._reload()
        self.wait(lambda: len(self.dialog._entries) == 4 and not self.dialog._busy)
        return entries

    def key(self, code, *, control=False, shift=False, hook=False):
        event = wx.KeyEvent(wx.EVT_CHAR_HOOK.typeId if hook else wx.EVT_KEY_DOWN.typeId)
        event.SetKeyCode(code)
        event.SetControlDown(control)
        event.SetShiftDown(shift)
        owner = self.dialog if hook else self.dialog.items
        owner.GetEventHandler().ProcessEvent(event)

    def test_native_shift_range_shrinks_and_ctrl_space_marks_without_losing_other_rows(self):
        entries = self.batch_entries()
        self.dialog.items.Select(0, False)
        self.dialog.items.Select(1)
        self.dialog.items.Focus(1)
        for key, indices in ((wx.WXK_DOWN, (1, 2)), (wx.WXK_DOWN, (1, 2, 3)),
                             (wx.WXK_UP, (1, 2)), (wx.WXK_UP, (1,))):
            with self.subTest(key=key, indices=indices):
                self.key(key, shift=True)
                self.assertEqual(self.dialog._selected_entries(), [entries[i] for i in indices])
                self.assertEqual(self.dialog.items.GetSelectedItemCount(), len(indices))
        self.key(wx.WXK_DOWN)
        self.assertEqual(self.dialog.items.GetFocusedItem(), 2)
        self.assertEqual(self.dialog._selected_entries(), [entries[1]])
        self.key(wx.WXK_SPACE, control=True)
        self.assertEqual(self.dialog._selected_entries(), entries[1:3])
        self.assertTrue(self.dialog.items.GetItemText(2).startswith("Seleccionado."))
        self.assertTrue(self.dialog.items.GetItemText(0).startswith("No seleccionado."))
        self.key(wx.WXK_SPACE)
        self.assertEqual(self.dialog._selected_entries(), [entries[1]])
        self.assertEqual(self.dialog.details.GetValue(), entries[2].description)
        self.key(wx.WXK_ESCAPE)
        self.assertFalse(self.dialog._selection_mode)
        self.assertEqual(self.dialog._selected(), entries[2])
        self.assertTrue(self.dialog._active)
        self.assertFalse(self.dialog.items.GetItemText(0).startswith("No seleccionado."))

    def test_char_hook_consumes_marking_and_enter_never_sends_a_batch(self):
        entries = self.batch_entries()
        with patch.object(self.dialog.items, "HasFocus", return_value=True):
            self.key(wx.WXK_SPACE, control=True, hook=True)
            self.key(wx.WXK_DOWN, hook=True)
            self.key(wx.WXK_SPACE, control=True, hook=True)
        self.assertEqual(self.dialog._selected_entries(), entries[:2])
        self.assertFalse(self.dialog.send_button.IsEnabled())
        with patch.object(self.dialog, "EndModal") as end, patch.object(
            self.dialog, "_context_menu"
        ) as menu:
            self.key(wx.WXK_RETURN)
            self.dialog._send()
        menu.assert_called_once_with()
        end.assert_not_called()
        self.assertIsNone(self.dialog.selected_sticker)
        self.key(wx.WXK_UP)
        self.key(wx.WXK_SPACE, control=True)
        self.key(wx.WXK_DOWN)
        self.key(wx.WXK_SPACE, control=True)
        self.assertEqual(self.dialog._selected_entries(), [])
        self.assertTrue(self.dialog._selection_mode)
        with patch.object(self.dialog, "PopupMenu") as popup:
            self.dialog._context_menu()
        popup.assert_not_called()

    def test_selection_survives_reload_but_filters_and_pages_cancel_hidden_marks(self):
        entries = self.batch_entries()
        self.dialog._set_selection({entries[0].id, entries[2].id}, 2)
        self.dialog._reload()
        self.wait(lambda: not self.dialog._busy)
        self.assertEqual(self.dialog._selected_entries(), [entries[0], entries[2]])
        self.assertEqual(self.dialog.items.GetFocusedItem(), 2)
        self.dialog.search.SetValue("Fixture 3")
        self.dialog._search()
        self.wait(lambda: not self.dialog._busy)
        self.assertFalse(self.dialog._selection_mode)
        self.assertEqual(self.dialog._selected(), entries[3])
        self.dialog._set_selection({entries[3].id}, 0)
        self.dialog._offset = 100
        self.dialog._previous()
        self.wait(lambda: not self.dialog._busy)
        self.assertFalse(self.dialog._selection_mode)
        self.assertEqual(self.dialog._marked_ids, set())

    def test_native_mouse_multiselection_uses_counted_menu_and_single_actions_are_absent(self):
        entries = self.batch_entries()
        self.dialog.items.Select(2)
        self.assertEqual(self.dialog._selected_entries(), [entries[0], entries[2]])
        actions = {}
        with patch.object(self.dialog, "PopupMenu", side_effect=lambda menu: actions.update({
            item.GetItemLabelText(): item.IsEnabled() for item in menu.GetMenuItems()
        })):
            self.dialog._context_menu()
        self.assertTrue(actions["Eliminar de la biblioteca (2)..."])
        self.assertFalse(actions["Mover a paquete (2)..."])
        self.assertFalse(actions["Quitar de este paquete (2)"])
        self.assertNotIn("Enviar sticker", actions)
        self.assertNotIn("Describir con RayoAI...", actions)
        self.dialog._busy = True
        try:
            with patch.object(self.dialog, "PopupMenu") as popup:
                self.dialog._context_menu()
            popup.assert_not_called()
        finally:
            self.dialog._busy = False

    def test_selection_navigation_bounds_select_page_and_escape_from_search(self):
        entries = self.batch_entries()
        with patch.object(self.dialog.speaker, "speak") as speak:
            self.key(wx.WXK_SPACE, control=True)
            speak.assert_called_once_with("Seleccionado. Fixture 0. 1 stickers seleccionados")
        for code, index in ((wx.WXK_UP, 0), (wx.WXK_END, 3), (wx.WXK_DOWN, 3),
                            (wx.WXK_HOME, 0), (wx.WXK_RIGHT, 1), (wx.WXK_LEFT, 0),
                            (wx.WXK_PAGEDOWN, 3), (wx.WXK_PAGEUP, 0)):
            with self.subTest(code=code, index=index):
                self.key(code)
                self.assertEqual(self.dialog.items.GetFocusedItem(), index)
                self.assertEqual(self.dialog._selected_entries(), [entries[0]])
        self.key(ord("A"), control=True)
        self.assertEqual(self.dialog._selected_entries(), entries)
        with patch.object(self.dialog.items, "HasFocus", return_value=False):
            self.key(wx.WXK_ESCAPE, hook=True)
        self.assertFalse(self.dialog._selection_mode)
        self.assertTrue(self.dialog._active)
        self.dialog._loaded(([], self.dialog._packs, {}, None, False))
        for code, control, shift in ((wx.WXK_SPACE, True, False),
                                     (wx.WXK_DOWN, False, True), (ord("A"), True, False)):
            with self.subTest(code=code):
                self.key(code, control=control, shift=shift)
        self.assertEqual(self.dialog._marked_ids, set())

    def test_queued_package_operation_uses_snapshot_not_later_marks(self):
        entries = self.batch_entries()
        target = self.library.create_pack("Fixture pack")
        self.dialog._reload()
        self.wait(lambda: not self.dialog._busy)
        ids = {entries[0].id, entries[2].id}
        self.dialog._set_selection(ids, 0)
        chooser = SimpleNamespace(ShowModal=lambda: wx.ID_OK, GetSelection=lambda: 0,
                                  Destroy=Mock())
        with patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.SingleChoiceDialog",
                   return_value=chooser), patch.object(self.dialog, "_run") as run:
            self.dialog._assign()
        self.dialog._set_selection({entries[1].id}, 1)
        run.call_args.args[0]()
        self.assertEqual({entry.id for entry in self.library.list_stickers(pack_id=target)}, ids)

    def test_batch_add_move_unassign_preserves_other_memberships_and_selection_snapshot(self):
        entries = self.batch_entries()
        source, target, other = [self.library.create_pack(name)
                                 for name in ("A Source", "B Target", "C Other")]
        ids = [entries[i].id for i in (0, 2)]
        self.library.assign_many_pack(ids, source)
        self.library.assign_many_pack(ids, other)
        self.dialog._reload()
        self.wait(lambda: not self.dialog._busy)
        self.dialog._set_selection(set(ids), 2)
        chooser = SimpleNamespace(ShowModal=lambda: wx.ID_OK, GetSelection=lambda: 1,
                                  Destroy=Mock())
        with patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.SingleChoiceDialog",
                   return_value=chooser):
            self.dialog._assign()
            self.wait(lambda: not self.dialog._busy)
        self.assertEqual([self.library.get(value).pack_ids for value in ids],
                         [(source, target, other)] * 2)
        self.dialog.groups.SetSelection(2)
        self.dialog._search()
        self.wait(lambda: not self.dialog._busy)
        self.dialog._set_selection(set(ids), 0)
        chooser.GetSelection = lambda: 0
        with patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.SingleChoiceDialog",
                   return_value=chooser):
            self.dialog._move()
            self.wait(lambda: not self.dialog._busy)
        self.assertEqual(self.dialog._entries, [])
        self.assertEqual([self.library.get(value).pack_ids for value in ids],
                         [(target, other)] * 2)
        self.dialog.groups.SetSelection(3)
        self.dialog._search()
        self.wait(lambda: not self.dialog._busy)
        self.dialog._set_selection(set(ids), 0)
        self.dialog._unassign()
        self.wait(lambda: not self.dialog._busy)
        self.assertEqual([self.library.get(value).pack_ids for value in ids], [(other,)] * 2)
        self.assertEqual(self.library.get(entries[1].id).pack_ids, ())
        self.assertTrue(all(Path(entry.path).exists() for entry in entries))

    def test_batch_dialog_cancellation_and_delete_partial_failure_refresh_for_retry(self):
        entries = self.batch_entries()
        self.library.create_pack("Fixture pack")
        self.dialog._reload()
        self.wait(lambda: not self.dialog._busy)
        ids = {entries[0].id, entries[2].id}
        self.dialog._set_selection(ids, 0)
        chooser = SimpleNamespace(ShowModal=lambda: wx.ID_CANCEL, Destroy=Mock())
        with patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.SingleChoiceDialog",
                   return_value=chooser), patch.object(self.dialog, "_run") as run:
            self.dialog._assign()
        run.assert_not_called()
        with patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.MessageBox",
                   return_value=wx.NO), patch.object(self.dialog, "_run") as run:
            self.dialog._delete()
        run.assert_not_called()
        original_delete = self.library.delete

        def fail_one(value):
            if value == entries[2].id:
                raise PermissionError("fixture in use")
            original_delete(value)

        with patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.MessageBox",
                   return_value=wx.YES) as warning, patch.object(
                       self.library, "delete", side_effect=fail_one
                   ):
            self.dialog._delete()
            self.wait(lambda: not self.dialog._busy)
        self.assertEqual(warning.call_count, 2)
        self.assertIn("Eliminados 1 de 2", warning.call_args.args[0])
        self.assertEqual(self.dialog._marked_ids, {entries[2].id})
        self.assertEqual(self.dialog._selected_entries(), [entries[2]])
        self.assertEqual(len(self.dialog._entries), 3)
        with patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.MessageBox", return_value=wx.YES):
            self.dialog._delete()
            self.wait(lambda: not self.dialog._busy)
        self.assertEqual(len(self.dialog._entries), 2)

    def test_manual_creation_gallery_preview_and_context_actions(self) -> None:
        with (
            patch(
                "cliente_xmpp.ui.sticker_gallery_dialog.StickerDescriptionDialog",
                return_value=self.choice(0),
            ),
            patch.object(self.dialog, "_text", return_value="Una figura saluda."),
            patch("cliente_xmpp.ui.sticker_gallery_dialog.rayoai.request_description") as ai,
        ):
            self.dialog._create(lambda: self.source)
            self.wait(lambda: len(self.dialog._entries) == 1 and not self.dialog._busy)
        ai.assert_not_called()
        self.assertEqual(self.dialog.items.GetItemCount(), 1)
        self.assertEqual(self.dialog.details.GetValue(), "Una figura saluda.")
        self.assertTrue(self.dialog.preview.GetBitmap().IsOk())
        labels = []
        with patch.object(
            self.dialog,
            "PopupMenu",
            side_effect=lambda menu: labels.extend(
                item.GetItemLabelText() for item in menu.GetMenuItems()
            ),
        ):
            self.dialog._context_menu()
        self.assertIn("Editar descripción...", labels)
        self.assertIn("Describir con RayoAI...", labels)
        self.assertIn("Añadir a favoritos", labels)
        self.assertIn("Añadir a paquete...", labels)
        self.assertIn("Eliminar de la biblioteca...", labels)

    def test_no_description_choice_does_not_contact_rayoai(self) -> None:
        with (
            patch(
                "cliente_xmpp.ui.sticker_gallery_dialog.StickerDescriptionDialog",
                return_value=self.choice(2),
            ),
            patch("cliente_xmpp.ui.sticker_gallery_dialog.rayoai.request_description") as ai,
        ):
            self.dialog._create(lambda: self.source)
            self.wait(lambda: bool(self.dialog._entries) and not self.dialog._busy)
        ai.assert_not_called()
        self.assertEqual(self.dialog._entries[0].description, "")

    def test_automatic_rayoai_and_reversible_preference(self) -> None:
        self.dialog._auto_describe = True
        self.dialog._auto_changed()
        with (
            patch(
                "cliente_xmpp.ui.sticker_gallery_dialog.rayoai.request_description",
                return_value="Descripción automática",
            ) as ai,
            patch("cliente_xmpp.ui.sticker_gallery_dialog.StickerDescriptionDialog") as question,
        ):
            self.dialog._create(lambda: self.source)
            self.wait(lambda: bool(self.dialog._entries) and not self.dialog._busy)
        question.assert_not_called()
        self.assertTrue(Path(ai.call_args.args[0]).is_relative_to(self.library.files))
        self.assertEqual(self.dialog.details.GetValue(), "Descripción automática")
        self.dialog._auto_describe = False
        self.dialog._auto_changed()
        self.assertFalse(self.settings.load_sticker_auto_describe())

    def test_existing_description_is_saved_without_question_or_ai_even_when_automatic(self) -> None:
        for automatic in (False, True):
            with self.subTest(automatic=automatic):
                self.dialog._auto_describe = automatic
                with (
                    patch(
                        "cliente_xmpp.ui.sticker_gallery_dialog.StickerDescriptionDialog"
                    ) as question,
                    patch(
                        "cliente_xmpp.ui.sticker_gallery_dialog.rayoai.request_description"
                    ) as ai,
                ):
                    self.dialog._create(lambda: self.source, "  Texto alternativo recibido  ")
                    self.wait(lambda: bool(self.dialog._entries) and not self.dialog._busy)
                ai.assert_not_called()
                question.assert_not_called()
                self.assertEqual(self.dialog.details.GetValue(), "Texto alternativo recibido")

    def test_rayoai_failure_preserves_created_sticker_and_allows_manual_edit(self) -> None:
        self.dialog._auto_describe = True
        with (
            patch(
                "cliente_xmpp.ui.sticker_gallery_dialog.rayoai.request_description",
                return_value=None,
            ),
            patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.MessageBox") as error,
        ):
            self.dialog._create(lambda: self.source)
            self.wait(lambda: bool(self.dialog._entries) and not self.dialog._busy)
        error.assert_called_once()
        self.assertEqual(self.dialog._entries[0].description, "")
        with patch.object(self.dialog, "_text", return_value="Corrección manual"):
            self.dialog._edit_description()
            self.wait(
                lambda: (
                    self.dialog.details.GetValue() == "Corrección manual" and not self.dialog._busy
                )
            )

    def test_late_worker_result_ignores_closed_dialog(self) -> None:
        started, finish = threading.Event(), threading.Event()
        callback = Mock()

        def worker():
            started.set()
            finish.wait(3)
            return "late result"

        self.dialog._run(worker, callback)
        self.assertTrue(started.wait(1))
        self.dialog._shutdown()
        finish.set()
        self.dialog._executor.shutdown(wait=True)
        self.app.Yield()
        callback.assert_not_called()

    def test_invalid_or_failed_ai_response_still_refreshes_saved_sticker(self) -> None:
        self.dialog._auto_describe = True
        for response in ("x" * 8001, RuntimeError("IPC unavailable")):
            with (
                self.subTest(response_type=type(response).__name__),
                patch(
                    "cliente_xmpp.ui.sticker_gallery_dialog.rayoai.request_description",
                    **(
                        {"side_effect": response}
                        if isinstance(response, Exception)
                        else {"return_value": response}
                    ),
                ),
                patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.MessageBox") as warning,
            ):
                self.dialog._create(lambda: self.source)
                self.wait(lambda: bool(self.dialog._entries) and not self.dialog._busy)
                warning.assert_called_once()
                self.assertEqual(self.dialog.details.GetValue(), "")
                self.assertTrue(Path(self.dialog._entries[0].path).exists())

    def test_export_format_filter_changes_extension_and_confirms_actual_target(self) -> None:
        pack_id = self.library.create_pack("Fixture pack")
        self.dialog._packs = self.library.packs()
        self.dialog.groups.SetItems(["Todos", "Favoritos", "Fixture pack"])
        self.dialog.groups.SetSelection(2)
        original = self.root / "fixture.canstickers"
        target = original.with_suffix(".wastickers")
        target.write_bytes(b"existing package")
        chooser = SimpleNamespace(
            ShowModal=lambda: wx.ID_OK,
            GetPath=lambda: str(original),
            GetFilterIndex=lambda: 1,
            Destroy=Mock(),
        )
        with (
            patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.FileDialog", return_value=chooser),
            patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.MessageBox", return_value=wx.NO),
            patch.object(self.dialog, "_run") as run,
        ):
            self.dialog._export()
            run.assert_not_called()
        self.assertEqual(target.read_bytes(), b"existing package")
        with (
            patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.FileDialog", return_value=chooser),
            patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.MessageBox", return_value=wx.YES),
            patch("cliente_xmpp.ui.sticker_gallery_dialog.export_pack") as export,
            patch.object(self.dialog, "_run", side_effect=lambda operation, _: operation()),
        ):
            self.dialog._export()
            export.assert_called_once_with(self.library, pack_id, target)

    def test_description_choice_only_remembers_rayoai(self) -> None:
        choice = StickerDescriptionDialog(self.dialog)
        try:
            self.assertFalse(choice.always.IsEnabled())
            choice.choice.SetSelection(1)
            choice._choice_changed(None)
            self.assertTrue(choice.always.IsEnabled())
            choice.always.SetValue(True)
            choice.choice.SetSelection(2)
            choice._choice_changed(None)
            self.assertFalse(choice.always.GetValue())
            self.assertFalse(choice.always.IsEnabled())
        finally:
            choice.Destroy()

    def test_simple_surface_keeps_advanced_actions_in_accessible_library_menu(self) -> None:
        self.assertEqual(self.dialog.items.GetColumnCount(), 2)
        self.assertFalse(self.dialog.previous_button.IsShown())
        self.assertFalse(self.dialog.next_button.IsShown())
        labels = [
            child.GetLabel()
            for child in self.dialog.GetChildren()
            if isinstance(child, wx.Button) and child.IsShown()
        ]
        self.assertEqual(
            labels, ["&Crear...", "&Acciones...", "&Biblioteca...", "&Enviar", "Cerrar"]
        )
        self.assertFalse(self.dialog.send_button.IsEnabled())
        self.assertFalse(any(isinstance(child, wx.CheckBox) for child in self.dialog.GetChildren()))
        actions = {}
        with patch.object(
            self.dialog,
            "PopupMenu",
            side_effect=lambda menu: actions.update(
                {
                    item.GetItemLabelText(): item.IsEnabled()
                    for item in menu.GetMenuItems()
                    if not item.IsSeparator()
                }
            ),
        ):
            self.dialog._library_menu()
        self.assertTrue(actions["Importar paquete..."])
        self.assertTrue(actions["Nuevo paquete..."])
        self.assertTrue(actions["Preferencias..."])
        self.assertTrue(actions["Cómo usar la galería..."])
        self.assertFalse(actions["Exportar paquete..."])
        self.assertFalse(actions["Compartir paquete en el chat..."])

    def test_library_menu_has_package_actions_and_preferences_are_reversible(self) -> None:
        self.library.create_pack("Fixture pack")
        self.dialog._reload()
        self.wait(lambda: len(self.dialog._packs) == 1 and not self.dialog._busy)
        self.dialog.groups.SetSelection(2)
        self.dialog._on_share = Mock()
        actions = {}
        with patch.object(
            self.dialog,
            "PopupMenu",
            side_effect=lambda menu: actions.update(
                {
                    item.GetItemLabelText(): item.IsEnabled()
                    for item in menu.GetMenuItems()
                    if not item.IsSeparator()
                }
            ),
        ):
            self.dialog._library_menu()
        for label in (
            "Renombrar paquete...",
            "Eliminar paquete...",
            "Exportar paquete...",
            "Compartir paquete en el chat...",
        ):
            self.assertTrue(actions[label])
        for enabled in (True, False):
            preferences = SimpleNamespace(
                ShowModal=lambda: wx.ID_OK,
                Destroy=Mock(),
                auto_describe=SimpleNamespace(GetValue=lambda enabled=enabled: enabled),
            )
            with patch(
                "cliente_xmpp.ui.sticker_gallery_dialog.StickerGallerySettingsDialog",
                return_value=preferences,
            ):
                self.dialog._preferences()
            self.assertEqual(self.settings.load_sticker_auto_describe(), enabled)
            preferences.Destroy.assert_called_once()
        real_preferences = StickerGallerySettingsDialog(self.dialog, True)
        try:
            self.assertTrue(real_preferences.auto_describe.GetValue())
            labels = [
                child.GetLabel()
                for child in real_preferences.GetChildren()
                if isinstance(child, wx.StaticText)
            ]
            self.assertTrue(any("proveedor externo" in label for label in labels))
        finally:
            real_preferences.Destroy()

    def test_manage_mode_has_no_inert_send_button_and_pagination_is_contextual(self) -> None:
        dialog = StickerGalleryDialog(self.frame, self.library, self.settings)
        try:
            self.wait(lambda: not dialog._busy and dialog.status.GetLabel() != "Cargando...")
            self.assertIsNone(dialog.send_button)
            entries = [self.library.add(self.source)] * 100
            dialog._loaded((entries, [], {}, None, False))
            self.assertTrue(dialog.previous_button.IsShown())
            self.assertTrue(dialog.next_button.IsShown())
            self.assertFalse(dialog.previous_button.IsEnabled())
            self.assertTrue(dialog.next_button.IsEnabled())
        finally:
            dialog._shutdown()
            dialog._executor.shutdown(wait=True, cancel_futures=True)
            dialog.Destroy()

    def test_keyboard_search_and_full_description_reading(self) -> None:
        entry = self.library.add(self.source, description="Descripción completa de prueba.")
        self.dialog._reload()
        self.wait(lambda: bool(self.dialog._entries) and not self.dialog._busy)
        event = SimpleNamespace(
            GetKeyCode=lambda: wx.WXK_SPACE, ShiftDown=lambda: False, Skip=Mock()
        )
        with patch.object(self.dialog.speaker, "speak") as speak:
            self.dialog._key(event)
        speak.assert_called_once_with(entry.description)
        shortcut = SimpleNamespace(
            GetKeyCode=lambda: ord("F"), ControlDown=lambda: True, Skip=Mock()
        )
        with patch.object(self.dialog.search, "SetFocus") as focus:
            self.dialog._shortcut(shortcut)
        focus.assert_called_once()
        self.assertIsInstance(self.dialog.search, wx.SearchCtrl)
        self.assertTrue(self.dialog.search.IsSearchButtonVisible())
        self.dialog.search.SetValue("Sin coincidencias")
        button = wx.CommandEvent(wx.EVT_SEARCHCTRL_SEARCH_BTN.typeId, self.dialog.search.GetId())
        self.dialog.search.GetEventHandler().ProcessEvent(button)
        self.wait(lambda: not self.dialog._entries and not self.dialog._busy)
        self.assertFalse(self.dialog.send_button.IsEnabled())

    def test_reload_preserves_selected_sticker_and_does_not_steal_description_focus(self) -> None:
        first = self.library.add(self.source, name="A")
        second_image = self.root / "second.png"
        Image.new("RGBA", (32, 32), "blue").save(second_image)
        second = self.library.add(second_image, name="B")
        self.dialog._selected_id = second.id
        with (
            patch(
                "cliente_xmpp.ui.sticker_gallery_dialog.wx.Window.FindFocus",
                return_value=self.dialog.details,
            ),
            patch.object(self.dialog.items, "SetFocus") as focus,
        ):
            self.dialog._loaded(([first, second], [], {}, None, False))
        self.assertEqual(self.dialog._selected().id, second.id)
        focus.assert_not_called()

    def test_first_show_schedules_list_focus_once_after_modal_default_focus(self) -> None:
        self.dialog._initial_list_focus_pending = True
        event = SimpleNamespace(IsShown=lambda: True, Skip=Mock())
        with patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.CallAfter") as schedule:
            self.dialog._shown(event)
            self.dialog._shown(event)
        schedule.assert_called_once_with(self.dialog._focus_initial_list)
        self.assertFalse(self.dialog._initial_list_focus_pending)
        self.assertEqual(event.Skip.call_count, 2)

    def test_opening_modal_gallery_focuses_the_list_not_search(self) -> None:
        entry = self.library.add(self.source, name="Sticker de prueba")
        self.dialog._reload()
        self.wait(lambda: bool(self.dialog._entries) and not self.dialog._busy)
        focused = []
        self.dialog.Move((-10000, -10000))

        def inspect_and_close() -> None:
            focused.append(wx.Window.FindFocus())
            self.dialog.EndModal(wx.ID_CANCEL)

        timer = wx.CallLater(100, inspect_and_close)
        try:
            self.dialog.ShowModal()
        finally:
            timer.Stop()
        self.assertEqual(focused, [self.dialog.items])
        self.assertEqual(self.dialog._selected().id, entry.id)

    def test_alt_m_focuses_list_and_keeps_selection_even_during_reload(self) -> None:
        first = self.library.add(self.source, name="A")
        second_image = self.root / "focus-second.png"
        Image.new("RGBA", (32, 32), "blue").save(second_image)
        second = self.library.add(second_image, name="B")
        self.dialog._selected_id = second.id
        self.dialog._loaded(([first, second], [], {}, None, False))
        self.dialog._busy = True
        event = SimpleNamespace(
            GetKeyCode=lambda: ord("M"), AltDown=lambda: True,
            ControlDown=lambda: False, ShiftDown=lambda: False, Skip=Mock(),
        )
        with patch.object(self.dialog.items, "SetFocus") as focus:
            self.dialog._shortcut(event)
        focus.assert_called_once_with()
        self.assertEqual(self.dialog._selected().id, second.id)
        event.Skip.assert_not_called()

    def test_alt_m_can_focus_an_empty_list_and_does_not_select_a_missing_row(self) -> None:
        with patch.object(self.dialog.items, "SetFocus") as focus:
            self.dialog._focus_list()
        focus.assert_called_once_with()
        self.assertEqual(self.dialog.items.GetFirstSelected(), wx.NOT_FOUND)

    def test_initial_focus_callback_does_not_touch_controls_after_shutdown(self) -> None:
        self.dialog._shutdown()
        with patch.object(self.dialog.items, "SetFocus") as focus:
            self.dialog._focus_initial_list()
        focus.assert_not_called()

    def test_initial_focus_does_not_steal_focus_from_an_open_child_dialog(self) -> None:
        with (
            patch.object(self.dialog, "IsShown", return_value=True),
            patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.Window.FindFocus",
                  return_value=self.dialog.details),
            patch("cliente_xmpp.ui.sticker_gallery_dialog.wx.GetTopLevelParent",
                  return_value=Mock()),
            patch.object(self.dialog.items, "SetFocus") as focus,
        ):
            self.dialog._focus_initial_list()
        focus.assert_not_called()

    def test_alt_m_does_not_handle_unmodified_m_or_ctrl_alt_m(self) -> None:
        modifiers = ((False, False, False), (True, True, False), (True, False, True))
        for alt, control, shift in modifiers:
            with self.subTest(alt=alt, control=control, shift=shift):
                event = SimpleNamespace(
                    GetKeyCode=lambda: ord("M"), AltDown=Mock(return_value=alt),
                    ControlDown=Mock(return_value=control), ShiftDown=Mock(return_value=shift),
                    Skip=Mock(),
                )
                with patch.object(self.dialog.items, "SetFocus") as focus:
                    self.dialog._shortcut(event)
                focus.assert_not_called()
                event.Skip.assert_called_once_with()

    def test_filter_stays_enabled_and_latest_choice_wins_during_slow_reload(self) -> None:
        entry = self.library.add(self.source, name="Favorite")
        self.library.edit(entry.id, favorite=True)
        started, finish = threading.Event(), threading.Event()
        original = self.library.list_stickers
        calls = []

        def delayed(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                started.set()
                finish.wait(3)
            return original(**kwargs)

        with (
            patch.object(self.library, "list_stickers", side_effect=delayed),
            patch(
                "cliente_xmpp.ui.sticker_gallery_dialog.wx.Window.FindFocus",
                return_value=self.dialog.groups,
            ),
            patch.object(self.dialog.items, "SetFocus") as focus,
            patch.object(self.dialog.groups, "SetItems") as reset_choices,
        ):
            self.dialog._reload()
            self.assertTrue(started.wait(1))
            try:
                self.assertTrue(self.dialog.groups.IsEnabled())
                self.assertTrue(self.dialog.search.IsEnabled())
                for selection in (1, 0, 1):
                    self.dialog.groups.SetSelection(selection)
                    event = wx.CommandEvent(wx.EVT_CHOICE.typeId, self.dialog.groups.GetId())
                    self.dialog.groups.GetEventHandler().ProcessEvent(event)
                self.dialog.search.SetValue("Favorite")
                self.dialog._search()
            finally:
                finish.set()
            self.wait(lambda: not self.dialog._busy)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[-1]["favorites"])
        self.assertEqual(calls[-1]["query"], "Favorite")
        self.assertEqual(self.dialog.groups.GetSelection(), 1)
        self.assertEqual(self.dialog._selected().id, entry.id)
        focus.assert_not_called()
        reset_choices.assert_not_called()

    def test_f2_renames_selected_sticker_and_is_inert_while_busy(self) -> None:
        entry = self.library.add(self.source, name="Before")
        self.dialog._reload()
        self.wait(lambda: bool(self.dialog._entries) and not self.dialog._busy)
        event = wx.KeyEvent(wx.EVT_KEY_DOWN.typeId)
        event.SetKeyCode(wx.WXK_F2)
        with patch.object(self.dialog, "_text", return_value="After") as text:
            self.dialog.items.GetEventHandler().ProcessEvent(event)
            self.wait(lambda: not self.dialog._busy)
            self.assertEqual(self.library.get(entry.id).name, "After")
            self.assertEqual(self.dialog._selected().id, entry.id)
            text.assert_called_once_with("Nombre del sticker", "Before")
        self.dialog._busy = True
        try:
            with patch.object(self.dialog, "_text") as text:
                self.dialog._key(event)
            text.assert_not_called()
        finally:
            self.dialog._busy = False


if __name__ == "__main__":
    unittest.main()
