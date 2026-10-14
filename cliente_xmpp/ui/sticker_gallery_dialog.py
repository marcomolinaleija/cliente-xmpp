from __future__ import annotations

import io
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import wx
from PIL import Image, ImageOps

from cliente_xmpp.accessibility.speaker import NvdaSpeaker
from cliente_xmpp.config.settings import SettingsStore
from cliente_xmpp.integrations import rayoai
from cliente_xmpp.media.downloads import DOWNLOADS_DIR
from cliente_xmpp.media.sticker_packs import export_pack, import_pack
from cliente_xmpp.models.stickers import LibrarySticker, StickerPack
from cliente_xmpp.storage.sticker_library import MAX_DESCRIPTION_LENGTH, StickerLibrary
from cliente_xmpp.ui.theme import apply_theme

PAGE_SIZE = 100


def thumbnail(path: str) -> bytes:
    """Decode/resize off the wx thread; only native bitmaps are created in wx."""
    with Image.open(path) as image:
        frame = ImageOps.pad(image.convert("RGBA"), (128, 128), color=(0, 0, 0, 0))
        output = io.BytesIO()
        frame.save(output, format="PNG")
        return output.getvalue()


class StickerDescriptionDialog(wx.Dialog):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, title="Descripción del nuevo sticker", size=(620, 340))
        box = wx.BoxSizer(wx.VERTICAL)
        self.choice = wx.RadioBox(
            self,
            label="¿Cómo quieres añadir el texto alternativo?",
            choices=["Escribir manualmente", "Describir con RayoAI", "Sin descripción"],
            majorDimension=1,
            style=wx.RA_SPECIFY_COLS,
        )
        self.choice.SetSelection(2)
        box.Add(self.choice, 0, wx.EXPAND | wx.ALL, 12)
        self.always = wx.CheckBox(
            self, label="No volver a preguntar: usar RayoAI siempre para nuevos stickers"
        )
        self.always.Enable(False)
        box.Add(self.always, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        box.Add(
            wx.StaticText(
                self,
                label=(
                    "RayoAI recibirá la imagen y puede usar el proveedor externo "
                    "que tengas configurado.\n"
                    "Puedes desactivar la opción automática desde la galería."
                ),
            ),
            0,
            wx.ALL,
            12,
        )
        box.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALIGN_RIGHT | wx.ALL, 12)
        self.SetSizer(box)
        self.choice.Bind(wx.EVT_RADIOBOX, self._choice_changed)
        apply_theme(self)
        self.CenterOnParent()

    def _choice_changed(self, _event: wx.CommandEvent) -> None:
        selected = self.choice.GetSelection() == 1
        self.always.Enable(selected)
        if not selected:
            self.always.SetValue(False)


class StickerGallerySettingsDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, enabled: bool) -> None:
        super().__init__(parent, title="Preferencias de stickers")
        box = wx.BoxSizer(wx.VERTICAL)
        self.auto_describe = wx.CheckBox(self, label="Describir nuevos stickers con RayoAI siempre")
        self.auto_describe.SetValue(enabled)
        box.Add(self.auto_describe, 0, wx.ALL, 16)
        privacy = wx.StaticText(
            self,
            label="RayoAI recibirá la imagen y puede usar el proveedor externo configurado.\n"
            "Desmarca esta opción para elegir cómo describir cada nuevo sticker.",
        )
        box.Add(privacy, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)
        box.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALIGN_RIGHT | wx.ALL, 12)
        self.SetSizerAndFit(box)
        apply_theme(self)
        self.CenterOnParent()


class StickerGalleryDialog(wx.Dialog):
    def __init__(
        self,
        parent: wx.Window,
        library: StickerLibrary,
        settings: SettingsStore,
        *,
        can_send: bool = False,
        on_share: Callable[[Path], None] | None = None,
        initial_source: Callable[[], Path] | None = None,
        initial_description: str = "",
        initial_pack: Callable[[], Path] | None = None,
    ) -> None:
        super().__init__(
            parent,
            title="Galería de stickers de CAN",
            size=(900, 600),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.library, self.settings = library, settings
        self._can_send, self._on_share = can_send, on_share
        self._initial_source, self._initial_description = initial_source, initial_description
        self._initial_pack = initial_pack
        self._active, self._busy = True, False
        self._initial_list_focus_pending = True
        self._reload_pending = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="can-stickers")
        self._entries: list[LibrarySticker] = []
        self._packs: list[StickerPack] = []
        self._previews: dict[str, bytes] = {}
        self._offset = 0
        self._selected_id = ""
        self._selection_mode = False
        self._marked_ids: set[str] = set()
        self._selection_anchor: int | None = None
        self._range_base: set[str] = set()
        self._updating_selection = False
        self.selected_sticker: LibrarySticker | None = None
        self.speaker = NvdaSpeaker()
        self._buttons: list[wx.Button] = []
        self._auto_describe = self.settings.load_sticker_auto_describe()
        self._build()
        apply_theme(self)
        self.SetMinSize((760, 480))
        self.CenterOnParent()
        self.Bind(wx.EVT_CLOSE, self._close)
        self.Bind(wx.EVT_SHOW, self._shown)
        self.Bind(wx.EVT_BUTTON, self._close, id=wx.ID_CANCEL)
        wx.CallAfter(self._reload)

    def _button(self, row: wx.BoxSizer, label: str, handler: Callable) -> wx.Button:
        button = wx.Button(self, label=label)
        button.Bind(wx.EVT_BUTTON, handler)
        row.Add(button, 0, wx.RIGHT, 6)
        self._buttons.append(button)
        return button

    def _build(self) -> None:
        box = wx.BoxSizer(wx.VERTICAL)
        filters = wx.BoxSizer(wx.HORIZONTAL)
        filters.Add(
            wx.StaticText(self, label="&Buscar:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6
        )
        self.search = wx.SearchCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.search.ShowSearchButton(True)
        self.search.SetName("Buscar stickers por nombre o descripción; Enter para buscar")
        filters.Add(self.search, 2, wx.RIGHT, 12)
        filters.Add(
            wx.StaticText(self, label="M&ostrar:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6
        )
        self.groups = wx.Choice(self, choices=["Todos", "Favoritos"])
        self.groups.SetSelection(0)
        filters.Add(self.groups, 1)
        box.Add(filters, 0, wx.EXPAND | wx.ALL, 12)
        content = wx.BoxSizer(wx.HORIZONTAL)
        self.items = wx.ListCtrl(self, style=wx.LC_REPORT)
        self.items.SetName("Stickers")
        for index, (label, width) in enumerate((("Sticker", 220), ("Descripción", 270))):
            self.items.InsertColumn(index, label, width=width)
        content.Add(self.items, 1, wx.EXPAND | wx.RIGHT, 12)
        side = wx.BoxSizer(wx.VERTICAL)
        self.preview = wx.StaticBitmap(self, bitmap=wx.Bitmap(128, 128))
        self.preview.SetName("Vista previa del sticker seleccionado")
        side.Add(self.preview, 0, wx.ALIGN_CENTER | wx.BOTTOM, 8)
        side.Add(wx.StaticText(self, label="&Descripción:"), 0, wx.BOTTOM, 6)
        self.details = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(220, 150))
        side.Add(self.details, 1, wx.EXPAND)
        content.Add(side, 0, wx.EXPAND)
        box.Add(content, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        page = wx.BoxSizer(wx.HORIZONTAL)
        self.status = wx.StaticText(self, label="Cargando...")
        page.Add(self.status, 1, wx.ALIGN_CENTER_VERTICAL)
        self.previous_button = self._button(page, "Anterior", self._previous)
        self.next_button = self._button(page, "Siguiente", self._next)
        self.previous_button.Hide()
        self.next_button.Hide()
        box.Add(page, 0, wx.EXPAND | wx.ALL, 12)
        bottom = wx.BoxSizer(wx.HORIZONTAL)
        self._button(bottom, "&Crear...", self._create_file)
        self._button(bottom, "&Acciones...", self._context_menu)
        self._button(bottom, "&Biblioteca...", self._library_menu)
        bottom.AddStretchSpacer()
        self.send_button = self._button(bottom, "&Enviar", self._send) if self._can_send else None
        if self.send_button:
            self.send_button.SetDefault()
        bottom.Add(wx.Button(self, wx.ID_CANCEL, "Cerrar"), 0)
        box.Add(bottom, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.SetSizer(box)
        self.groups.Bind(wx.EVT_CHOICE, self._search)
        self.search.Bind(wx.EVT_TEXT_ENTER, self._search)
        self.search.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self._search)
        self.items.Bind(wx.EVT_LIST_ITEM_SELECTED, self._select)
        self.items.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._select)
        self.items.Bind(wx.EVT_LIST_ITEM_FOCUSED, self._focused)
        self.items.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._send)
        self.items.Bind(wx.EVT_CONTEXT_MENU, self._context_menu)
        self.items.Bind(wx.EVT_KEY_DOWN, self._key)
        self.Bind(wx.EVT_CHAR_HOOK, self._shortcut)
        self._enable(True)

    def _shown(self, event: wx.ShowEvent) -> None:
        if event.IsShown() and self._initial_list_focus_pending:
            self._initial_list_focus_pending = False
            # ShowModal assigns its default focus after the show event.
            wx.CallAfter(self._focus_initial_list)
        event.Skip()

    def _focus_initial_list(self) -> None:
        if not self._active or not self.IsShown():
            return
        focused = wx.Window.FindFocus()
        if focused is None or wx.GetTopLevelParent(focused) is self:
            self._focus_list()

    def _focus_list(self) -> None:
        if not self._active:
            return
        selected = self.items.GetFocusedItem()
        if selected == wx.NOT_FOUND and self.items.GetItemCount():
            selected = 0
            if not self._selection_mode:
                self.items.Select(selected)
        if selected != wx.NOT_FOUND:
            self.items.Focus(selected)
            self.items.EnsureVisible(selected)
        self.items.SetFocus()

    def _enable(self, enabled: bool, *, keep_filters: bool = False) -> None:
        for button in self._buttons:
            button.Enable(enabled)
        if self.send_button:
            self.send_button.Enable(
                enabled and not self._selection_mode and self._selected() is not None
            )
        self.previous_button.Enable(enabled and self._offset > 0)
        self.next_button.Enable(enabled and len(self._entries) == PAGE_SIZE)
        for control in (self.groups, self.search):
            control.Enable(enabled or keep_filters)

    def _run(
        self, operation: Callable, finished: Callable | None = None, *, keep_filters: bool = False
    ) -> None:
        if self._busy or not self._active:
            return
        self._busy = True
        self._enable(False, keep_filters=keep_filters)
        self.status.SetLabel("Procesando...")

        def worker() -> None:
            try:
                result, error = operation(), ""
            except Exception as exc:
                result, error = None, str(exc)
            wx.CallAfter(self._finish, result, error, finished)

        self._executor.submit(worker)

    def _finish(self, result: object, error: str, finished: Callable | None) -> None:
        if not self._active:
            return
        self._busy = False
        self._enable(True)
        if error:
            self.status.SetLabel(error)
            wx.MessageBox(error, "Galería de stickers", wx.OK | wx.ICON_WARNING, self)
            if self._reload_pending:
                self._reload_pending = False
                self._reload()
        elif finished:
            finished(result)
        else:
            self._reload()

    def _pack_id(self) -> int | None:
        index = self.groups.GetSelection() - 2
        return self._packs[index].id if 0 <= index < len(self._packs) else None

    def _reload(self) -> None:
        if not self._active:
            return
        if self._busy:
            self._reload_pending = True
            return
        pack_id, favorite, query, offset = (
            self._pack_id(),
            self.groups.GetSelection() == 1,
            self.search.GetValue(),
            self._offset,
        )

        def load():
            entries = self.library.list_stickers(
                pack_id=pack_id, favorites=favorite, query=query, offset=offset, limit=PAGE_SIZE
            )
            previews = {}
            for entry in entries:
                try:
                    previews[entry.id] = thumbnail(entry.path)
                except (OSError, ValueError):
                    pass
            return entries, self.library.packs(), previews, pack_id, favorite

        # Disabling a focused Choice moves keyboard focus to the sticker list.
        self._run(load, self._loaded, keep_filters=True)

    def _loaded(self, result) -> None:
        if self._reload_pending:
            self._reload_pending = False
            self._reload()
            return
        focused = wx.Window.FindFocus()
        entries, self._packs, self._previews, pack_id, favorite = result
        marked_ids = self._marked_ids & {entry.id for entry in entries}
        self._entries = entries
        groups = ["Todos", "Favoritos", *[f"{p.name} ({p.count})" for p in self._packs]]
        if self.groups.GetItems() != groups:
            self.groups.SetItems(groups)
        selection = next(
            (i + 2 for i, p in enumerate(self._packs) if p.id == pack_id), 1 if favorite else 0
        )
        self.groups.SetSelection(selection)
        images = wx.ImageList(48, 48)
        self.items.Freeze()
        self._updating_selection = True
        try:
            self.items.DeleteAllItems()
            for index, entry in enumerate(entries):
                icon = -1
                if entry.id in self._previews:
                    image = wx.Image(io.BytesIO(self._previews[entry.id]), wx.BITMAP_TYPE_PNG)
                    icon = images.Add(wx.Bitmap(image.Scale(48, 48)))
                qualifiers = [
                    word
                    for word, present in (("favorito", entry.favorite), ("animado", entry.animated))
                    if present
                ]
                label = ", ".join([entry.name[:100], *qualifiers])
                self.items.InsertItem(index, label, icon)
                self.items.SetItem(index, 1, " ".join(entry.description.split())[:100])
            self.items.AssignImageList(images, wx.IMAGE_LIST_SMALL)
            selected = next((i for i, e in enumerate(entries) if e.id == self._selected_id), 0)
            self._marked_ids = marked_ids
            self._selection_anchor = None
            if entries:
                for index, entry in enumerate(entries):
                    self.items.Select(
                        index, entry.id in marked_ids if self._selection_mode else index == selected
                    )
                self.items.Focus(selected)
                self.items.EnsureVisible(selected)
        finally:
            self._updating_selection = False
            self.items.Thaw()
        paged = self._offset > 0 or len(entries) == PAGE_SIZE
        self.previous_button.Show(paged)
        self.next_button.Show(paged)
        self._enable(True)
        self.Layout()
        self._refresh_selection_labels()
        self._show_details()
        if self._initial_source is not None:
            source, self._initial_source = self._initial_source, None
            wx.CallAfter(self._create, source, self._initial_description)
        elif self._initial_pack is not None:
            source, self._initial_pack = self._initial_pack, None
            wx.CallAfter(self._run, lambda: import_pack(self.library, source()))
        elif focused is None or not self.IsDescendant(focused):
            self.items.SetFocus()

    def _selected(self) -> LibrarySticker | None:
        entries = self._selected_entries()
        return entries[0] if len(entries) == 1 else None

    def _selected_entries(self) -> list[LibrarySticker]:
        if self._selection_mode:
            return [entry for entry in self._entries if entry.id in self._marked_ids]
        index = self.items.GetFirstSelected()
        return [self._entries[index]] if 0 <= index < len(self._entries) else []

    def _focused_entry(self) -> LibrarySticker | None:
        index = self.items.GetFocusedItem()
        return self._entries[index] if 0 <= index < len(self._entries) else self._selected()

    def _select(self, _event: wx.ListEvent) -> None:
        if self._updating_selection:
            return
        ids = set()
        index = self.items.GetFirstSelected()
        while index != wx.NOT_FOUND:
            ids.add(self._entries[index].id)
            index = self.items.GetNextSelected(index)
        self._marked_ids = ids
        self._selection_mode = self._selection_mode or len(ids) > 1
        self._selection_anchor = None
        self._refresh_selection_labels()
        self._show_details()

    def _focused(self, event: wx.ListEvent) -> None:
        if not self._updating_selection:
            self._show_details()
        event.Skip()

    def _refresh_selection_labels(self) -> None:
        for index, entry in enumerate(self._entries):
            qualifiers = [word for word, present in (
                ("favorito", entry.favorite), ("animado", entry.animated)
            ) if present]
            label = ", ".join([entry.name[:100], *qualifiers])
            if self._selection_mode:
                state = "Seleccionado" if entry.id in self._marked_ids else "No seleccionado"
                label = f"{state}. {label}"
            self.items.SetItem(index, 0, label)
        page = f"Página {self._offset // PAGE_SIZE + 1} · " if (
            self._offset > 0 or len(self._entries) == PAGE_SIZE
        ) else ""
        selection = f" · {len(self._marked_ids)} seleccionados" if self._selection_mode else ""
        self.status.SetLabel(f"{page}{len(self._entries)} stickers{selection}")

    def _set_selection(self, ids: set[str], focused: int) -> None:
        self._selection_mode = True
        self._marked_ids = ids
        self._updating_selection = True
        try:
            for index, entry in enumerate(self._entries):
                self.items.Select(index, entry.id in ids)
            self.items.Focus(focused)
            self.items.EnsureVisible(focused)
        finally:
            self._updating_selection = False
        self._refresh_selection_labels()
        self._show_details()
        entry = self._entries[focused]
        state = "Seleccionado" if entry.id in ids else "No seleccionado"
        self.speaker.speak(f"{state}. {entry.name[:100]}. {len(ids)} stickers seleccionados")

    def _cancel_selection(self) -> None:
        focused = self.items.GetFocusedItem()
        self._selection_mode = False
        self._marked_ids.clear()
        self._selection_anchor = None
        self._range_base.clear()
        self._updating_selection = True
        try:
            for index in range(self.items.GetItemCount()):
                self.items.Select(index, index == focused)
        finally:
            self._updating_selection = False
        self._refresh_selection_labels()
        self._show_details()

    def _show_details(self) -> None:
        entry = self._focused_entry()
        self._selected_id = entry.id if entry else ""
        self.details.ChangeValue(entry.description if entry else "")
        if self.send_button:
            self.send_button.Enable(
                not self._busy and not self._selection_mode and self._selected() is not None
            )
        if entry and entry.id in self._previews:
            self.preview.SetBitmap(wx.Bitmap(wx.Image(io.BytesIO(self._previews[entry.id]))))
        else:
            self.preview.SetBitmap(wx.Bitmap(128, 128))

    def _key(self, event: wx.KeyEvent) -> None:
        code = event.GetKeyCode()
        if self._selection_key(event):
            return
        if code == wx.WXK_SPACE:
            entry = self._focused_entry()
            if entry:
                self.speaker.speak(entry.description or entry.name)
        elif code == wx.WXK_DELETE:
            self._delete()
        elif code == wx.WXK_F2:
            self._rename()
        elif code == wx.WXK_F10 and event.ShiftDown():
            self._context_menu()
        else:
            event.Skip()

    def _selection_key(self, event: wx.KeyEvent) -> bool:
        code = event.GetKeyCode()
        control = getattr(event, "ControlDown", lambda: False)()
        shift = getattr(event, "ShiftDown", lambda: False)()
        if getattr(event, "AltDown", lambda: False)():
            return False
        if code == wx.WXK_ESCAPE and self._selection_mode:
            self._cancel_selection()
            self.speaker.speak("Selección de stickers cancelada")
            return True
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and self._selection_mode:
            self._context_menu()
            return True
        navigation = {
            wx.WXK_UP: -1, wx.WXK_LEFT: -1, wx.WXK_DOWN: 1, wx.WXK_RIGHT: 1,
            wx.WXK_PAGEUP: -max(1, self.items.GetCountPerPage()),
            wx.WXK_PAGEDOWN: max(1, self.items.GetCountPerPage()),
        }
        toggling = code == wx.WXK_SPACE and not shift and (control or self._selection_mode)
        navigating = code in (*navigation, wx.WXK_HOME, wx.WXK_END) and (
            shift or self._selection_mode
        ) and not control
        select_all = control and not shift and code == ord("A")
        if not (toggling or navigating or select_all):
            return False
        if not self._entries:
            return True
        focused = max(0, self.items.GetFocusedItem())
        ids = set(self._marked_ids) if self._selection_mode else set()
        if toggling:
            sticker_id = self._entries[focused].id
            ids.symmetric_difference_update({sticker_id})
            self._selection_anchor = None
        elif select_all:
            ids = {entry.id for entry in self._entries}
            self._selection_anchor = None
        else:
            target = 0 if code == wx.WXK_HOME else len(self._entries) - 1 if (
                code == wx.WXK_END
            ) else max(0, min(len(self._entries) - 1, focused + navigation[code]))
            if shift:
                if self._selection_anchor is None:
                    self._selection_anchor = focused
                    self._range_base = ids.copy()
                start, end = sorted((self._selection_anchor, target))
                ids = self._range_base | {entry.id for entry in self._entries[start:end + 1]}
            else:
                self._selection_anchor = None
            focused = target
        self._set_selection(ids, focused)
        return True

    def _shortcut(self, event: wx.KeyEvent) -> None:
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE and self._selection_mode and self._selection_key(event):
            return
        if self.items.HasFocus() and self._selection_key(event):
            return
        if (
            code == ord("M")
            and event.AltDown()
            and not event.ControlDown()
            and not event.ShiftDown()
        ):
            self._focus_list()
        elif event.ControlDown() and code == ord("F") and not self._busy:
            self.search.SetFocus()
            self.search.SelectAll()
        elif event.ControlDown() and code == wx.WXK_PAGEUP:
            self._previous()
        elif event.ControlDown() and code == wx.WXK_PAGEDOWN:
            self._next()
        else:
            event.Skip()

    def _search(self, _event=None) -> None:
        self._cancel_selection()
        self._offset = 0
        self._reload()

    def _previous(self, _event=None) -> None:
        if self._busy:
            return
        self._cancel_selection()
        self._offset = max(0, self._offset - PAGE_SIZE)
        self._reload()

    def _next(self, _event=None) -> None:
        if not self._busy and len(self._entries) == PAGE_SIZE:
            self._cancel_selection()
            self._offset += PAGE_SIZE
            self._reload()

    def _auto_changed(self, _event=None) -> None:
        try:
            self.settings.save_sticker_auto_describe(self._auto_describe)
        except OSError as exc:
            self._auto_describe = self.settings.load_sticker_auto_describe()
            wx.MessageBox(str(exc), "No se pudo guardar la preferencia", parent=self)

    def _preferences(self, _event=None) -> None:
        dialog = StickerGallerySettingsDialog(self, self._auto_describe)
        try:
            if dialog.ShowModal() == wx.ID_OK:
                self._auto_describe = dialog.auto_describe.GetValue()
                self._auto_changed()
        finally:
            dialog.Destroy()

    def _help(self, _event=None) -> None:
        wx.MessageBox(
            "Elige un sticker con las flechas. F2 renombra; Espacio lee la descripción; "
            "Enter envía si abriste desde un chat. Mayús+F10 o Acciones muestra su menú.\n\n"
            "Mayús+flechas selecciona un rango; Ctrl+Espacio activa la selección y marca "
            "o desmarca. En ese modo las flechas conservan las marcas y Espacio las cambia. "
            "Ctrl+A selecciona la página. Enter abre las acciones del lote, no envía. "
            "Escape cancela la selección; otro Escape cierra. Cambiar filtro o página "
            "cancela las marcas. Puedes añadir a un paquete, mover desde el paquete "
            "actual, quitar sólo de ese paquete o eliminar de la biblioteca.\n\n"
            "Crear convierte una foto sin modificar el original. Biblioteca permite "
            "importar, agrupar, exportar, compartir y configurar RayoAI. "
            "Alt+M enfoca la lista; Alt+O enfoca Mostrar; Ctrl+F busca; "
            "Ctrl+RePág/AvPág cambia de página. Escape cierra.\n\n"
            "La operación iniciada termina en segundo plano si cierras. "
            "El formato .wastickers necesita un importador móvil compatible.",
            "Usar la galería",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )

    def _library_menu(self, _event=None) -> None:
        if self._busy:
            return
        selected_pack = self._pack_id() is not None
        actions = [
            ("Importar paquete...", self._import, True),
            ("Nuevo paquete...", self._new_pack, True),
            None,
            ("Renombrar paquete...", self._rename_pack, selected_pack),
            ("Eliminar paquete...", self._delete_pack, selected_pack),
            ("Exportar paquete...", self._export, selected_pack),
            (
                "Compartir paquete en el chat...",
                self._share,
                selected_pack and self._on_share is not None,
            ),
            None,
            ("Preferencias...", self._preferences, True),
            ("Cómo usar la galería...", self._help, True),
        ]
        menu = wx.Menu()
        bindings = []
        try:
            for action in actions:
                if action is None:
                    menu.AppendSeparator()
                    continue
                label, handler, enabled = action
                item = menu.Append(wx.ID_ANY, label)
                item.Enable(enabled)
                self.Bind(wx.EVT_MENU, handler, id=item.GetId())
                bindings.append(item.GetId())
            self.PopupMenu(menu)
        finally:
            for item_id in bindings:
                self.Unbind(wx.EVT_MENU, id=item_id)
            menu.Destroy()

    def _text(self, title: str, value: str = "", *, multiline: bool = False) -> str | None:
        dialog = wx.TextEntryDialog(
            self,
            title,
            "Stickers",
            value,
            style=wx.OK | wx.CANCEL | (wx.TE_MULTILINE if multiline else 0),
        )
        try:
            dialog.SetMaxLength(MAX_DESCRIPTION_LENGTH if multiline else 128)
            return dialog.GetValue() if dialog.ShowModal() == wx.ID_OK else None
        finally:
            dialog.Destroy()

    def _create_file(self, _event=None) -> None:
        dialog = wx.FileDialog(
            self,
            "Crear sticker desde imagen",
            wildcard="Imágenes|*.png;*.jpg;*.jpeg;*.webp",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        try:
            path = Path(dialog.GetPath()) if dialog.ShowModal() == wx.ID_OK else None
        finally:
            dialog.Destroy()
        if path:
            self._create(lambda: path)

    def _create(self, source: Callable[[], Path], existing_description: str = "") -> None:
        if self._busy or not self._active:
            return
        mode, description = "existing", existing_description.strip()
        # Received alt text is authoritative, even with automatic AI enabled.
        if not description and self._auto_describe:
            mode = "rayoai"
        elif not description:
            dialog = StickerDescriptionDialog(self)
            try:
                if dialog.ShowModal() != wx.ID_OK:
                    return
                mode = ("manual", "rayoai", "none")[dialog.choice.GetSelection()]
                if dialog.always.GetValue():
                    self._auto_describe = True
                    self._auto_changed()
            finally:
                dialog.Destroy()
        if mode == "manual":
            description = self._text("Texto alternativo del sticker", description, multiline=True)
            if description is None:
                return
        elif mode in {"none", "rayoai"}:
            description = ""
        pack_id = self._pack_id()

        def create():
            path = source()
            entry = self.library.add(path, description=description)
            if pack_id is not None:
                self.library.assign_pack(entry.id, pack_id)
            error = ""
            if mode == "rayoai" and not entry.description:
                try:
                    generated = rayoai.request_description(entry.path)
                    if not generated:
                        raise ValueError("RayoAI no respondió.")
                    self.library.edit(
                        entry.id, description=generated, expected_revision=entry.revision
                    )
                except Exception as exc:
                    error = (
                        "Sticker guardado. No se pudo añadir la descripción de RayoAI; "
                        f"puedes editarla manualmente.\n{exc}"
                    )
            return entry.id, error

        def created(result):
            self._selected_id, error = result
            if error:
                wx.MessageBox(error, "Sticker creado", wx.OK | wx.ICON_INFORMATION, self)
            self._reload()

        self._run(create, created)

    def _context_menu(self, _event=None) -> None:
        entries = self._selected_entries()
        if not entries or self._busy:
            return
        menu = wx.Menu()
        entry = entries[0]
        actions = [] if self._selection_mode or len(entries) > 1 else [
            ("Editar descripción...", self._edit_description),
            ("Describir con RayoAI...", self._describe),
            ("Quitar de favoritos" if entry.favorite else "Añadir a favoritos", self._favorite),
            ("Renombrar sticker...", self._rename),
        ]
        suffix = f" ({len(entries)})" if self._selection_mode else ""
        actions.extend([
            (f"Añadir a paquete{suffix}...", self._assign),
            (f"Mover a paquete{suffix}...", self._move),
            (f"Quitar de este paquete{suffix}", self._unassign),
            (f"Eliminar de la biblioteca{suffix}...", self._delete),
        ])
        if self._can_send and not self._selection_mode:
            actions.insert(0, ("Enviar sticker", self._send))
        bindings = []
        try:
            for label, handler in actions:
                item = menu.Append(wx.ID_ANY, label)
                self.Bind(wx.EVT_MENU, handler, id=item.GetId())
                bindings.append(item.GetId())
                if handler in (self._unassign, self._move):
                    item.Enable(self._pack_id() is not None)
                elif handler == self._assign:
                    item.Enable(bool(self._packs))
            self.PopupMenu(menu)
        finally:
            for item_id in bindings:
                self.Unbind(wx.EVT_MENU, id=item_id)
            menu.Destroy()

    def _edit_description(self, _event=None) -> None:
        if entry := self._selected():
            value = self._text("Editar texto alternativo", entry.description, multiline=True)
            if value is not None:
                self._run(
                    lambda: self.library.edit(
                        entry.id, description=value, expected_revision=entry.revision
                    )
                )

    def _describe(self, _event=None) -> None:
        if not (entry := self._selected()):
            return

        def describe():
            description = rayoai.request_description(entry.path)
            if not description:
                raise ValueError("RayoAI no respondió. La descripción anterior se conserva.")
            return description

        def confirm(description):
            value = self._text("Revisar descripción de RayoAI", description, multiline=True)
            if value is not None:
                self._run(
                    lambda: self.library.edit(
                        entry.id, description=value, expected_revision=entry.revision
                    )
                )

        self._run(describe, confirm)

    def _favorite(self, _event=None) -> None:
        if entry := self._selected():
            self._run(lambda: self.library.edit(entry.id, favorite=not entry.favorite))

    def _rename(self, _event=None) -> None:
        if self._busy:
            return
        if entry := self._selected():
            value = self._text("Nombre del sticker", entry.name)
            if value is not None:
                self._run(lambda: self.library.edit(entry.id, name=value))

    def _assign(self, _event=None, *, move: bool = False) -> None:
        if self._busy or not (entries := self._selected_entries()):
            return
        source = self._pack_id() if move else None
        if move and source is None:
            return
        packs = [pack for pack in self._packs if pack.id != source]
        if not packs:
            self.status.SetLabel("Crea un paquete de destino desde Biblioteca.")
            return
        dialog = wx.SingleChoiceDialog(
            self, f"Elige un paquete para {len(entries)} stickers",
            "Mover stickers" if move else "Añadir stickers a paquete", [p.name for p in packs]
        )
        try:
            pack = packs[dialog.GetSelection()] if dialog.ShowModal() == wx.ID_OK else None
        finally:
            dialog.Destroy()
        if pack:
            ids = [entry.id for entry in entries]
            self._run(lambda: self.library.assign_many_pack(ids, pack.id, source_pack_id=source))

    def _move(self, _event=None) -> None:
        self._assign(move=True)

    def _unassign(self, _event=None) -> None:
        if self._busy:
            return
        if (entries := self._selected_entries()) and (pack_id := self._pack_id()) is not None:
            ids = [entry.id for entry in entries]
            self._run(lambda: self.library.assign_many_pack(ids, pack_id, remove=True))

    def _delete(self, _event=None) -> None:
        if self._busy or not (entries := self._selected_entries()):
            return
        question = (
            "¿Eliminar este sticker de la biblioteca y de sus paquetes?"
            if len(entries) == 1 else
            f"¿Eliminar {len(entries)} stickers de la biblioteca y de sus paquetes?"
        )
        if (
            wx.MessageBox(
                question + "\nNo se elimina la foto original ni los mensajes enviados.",
                "Eliminar sticker",
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
                self,
            )
            == wx.YES
        ):
            ids = [entry.id for entry in entries]

            def delete():
                try:
                    self.library.delete_many(ids)
                    return ""
                except Exception as exc:
                    return str(exc)

            self._run(delete, self._deleted)

    def _deleted(self, error: str) -> None:
        if error:
            wx.MessageBox(error, "Galería de stickers", wx.OK | wx.ICON_WARNING, self)
        # File removals can partially succeed; refresh even on failure for retry.
        self._reload()

    def _new_pack(self, _event=None) -> None:
        name = self._text("Nombre del nuevo paquete")
        if name is None:
            return
        author = self._text("Autor del paquete", "CAN")
        if author is not None:
            self._run(lambda: self.library.create_pack(name, author))

    def _rename_pack(self, _event=None) -> None:
        if (pack_id := self._pack_id()) is None:
            self.status.SetLabel("Selecciona un paquete en Mostrar.")
            return
        value = self._text(
            "Nombre del paquete", next(p.name for p in self._packs if p.id == pack_id)
        )
        if value is not None:
            self._run(lambda: self.library.rename_pack(pack_id, value))

    def _delete_pack(self, _event=None) -> None:
        if (pack_id := self._pack_id()) is not None and wx.MessageBox(
            "¿Eliminar este paquete? Los stickers permanecerán en la biblioteca.",
            "Eliminar paquete",
            wx.YES_NO | wx.NO_DEFAULT,
            self,
        ) == wx.YES:
            self._run(lambda: self.library.delete_pack(pack_id))

    def _export(self, _event=None) -> None:
        if (pack_id := self._pack_id()) is None:
            self.status.SetLabel("Selecciona un paquete en Mostrar para exportarlo.")
            return
        dialog = wx.FileDialog(
            self,
            "Exportar paquete de stickers",
            defaultFile="stickers",
            wildcard="Paquete CAN|*.canstickers|Importadores WhatsApp|*.wastickers",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        try:
            destination = Path(dialog.GetPath()) if dialog.ShowModal() == wx.ID_OK else None
            suffix = ".wastickers" if dialog.GetFilterIndex() == 1 else ".canstickers"
        finally:
            dialog.Destroy()
        if destination:
            if destination.suffix.lower() != suffix:
                destination = destination.with_suffix(suffix)
                # Native overwrite confirmation concerned the original path, not this one.
                if (
                    destination.exists()
                    and wx.MessageBox(
                        f"¿Reemplazar el archivo {destination.name}?",
                        "Exportar paquete",
                        wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
                        self,
                    )
                    != wx.YES
                ):
                    return
            self._run(
                lambda: export_pack(self.library, pack_id, destination),
                lambda _: self.status.SetLabel(
                    "Paquete exportado. .wastickers requiere un importador "
                    "compatible; CAN conserva las descripciones."
                ),
            )

    def _share(self, _event=None) -> None:
        if (pack_id := self._pack_id()) is None or self._on_share is None:
            self.status.SetLabel("Selecciona un paquete para compartirlo.")
            return
        if next((pack.count for pack in self.library.packs() if pack.id == pack_id), 0) > 60:
            self.status.SetLabel(
                "El envío nativo está limitado a 60 stickers por paquete. "
                "Divide el paquete o usa Exportar paquete para compartir el archivo completo."
            )
            return
        # Explicit native send, never an automatic publication of a public PEP node.
        destination = DOWNLOADS_DIR / f"paquete-{uuid.uuid4().hex}.canstickers"

        def share():
            destination.parent.mkdir(parents=True, exist_ok=True)
            return export_pack(self.library, pack_id, destination)

        self._run(share, self._shared)

    def _shared(self, path: Path) -> None:
        self._on_share(path)
        self.status.SetLabel(
            "Paquete entregado al cliente para enviar; comprueba su entrega en el chat."
        )

    def _import(self, _event=None) -> None:
        dialog = wx.FileDialog(
            self,
            "Importar paquete",
            wildcard="Paquetes|*.canstickers;*.wastickers",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        try:
            source = Path(dialog.GetPath()) if dialog.ShowModal() == wx.ID_OK else None
        finally:
            dialog.Destroy()
        if source:
            self._run(lambda: import_pack(self.library, source))

    def _send(self, _event=None) -> None:
        if (
            self._can_send and not self._busy and not self._selection_mode
            and (entry := self._selected())
        ):
            self.selected_sticker = entry
            self._shutdown()
            self.EndModal(wx.ID_OK)

    def _shutdown(self) -> None:
        self._active = False
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _close(self, _event=None) -> None:
        if self._active:
            self._shutdown()
            if self.IsModal():
                self.EndModal(wx.ID_CANCEL)
            else:
                self.Destroy()
