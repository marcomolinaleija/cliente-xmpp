from __future__ import annotations

import wx

from cliente_xmpp.config.settings import MAX_RECENT_EMOJIS, SettingsStore
from cliente_xmpp.models.emojis import EmojiEntry, emoji_catalog, matching_emojis

EMOJI_PAGE_SIZE = 100


class EmojiPickerDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, settings: SettingsStore) -> None:
        super().__init__(parent, title="Insertar emoji",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.settings = settings
        self._catalog, groups = emoji_catalog()
        self._by_value = {entry.value: entry for entry in self._catalog}
        favorites, recent = settings.load_emoji_preferences()
        self.favorites = [value for value in favorites if value in self._by_value]
        self.recent = [value for value in recent if value in self._by_value]
        self._sections = ["all", "favorites", "recent", *(group for group, _ in groups)]
        self._choices: list[EmojiEntry] = []

        self.search = wx.SearchCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.search.SetName("Buscar emoji por nombre o palabra clave")
        self.search.SetDescriptiveText("Buscar emojis en español")
        self.section = wx.Choice(self, choices=[
            "Todos", "Favoritos", "Recientes", *(label for _, label in groups),
        ])
        self.section.SetName("Mostrar emojis de")
        self.section.SetSelection(0)
        self.results = wx.ListBox(self, style=wx.LB_SINGLE)
        self.results.SetName("Emojis encontrados")
        self.load_more_button = wx.Button(self, label="Cargar más emojis")
        self.status = wx.StaticText(self, label="")
        self.favorite_button = wx.Button(self, label="Añadir a favoritos")
        self.favorite_button.SetToolTip("Añadir o quitar el emoji seleccionado. Control+D.")
        self.insert_button = wx.Button(self, wx.ID_OK, "Insertar")
        self.insert_button.SetDefault()
        self.cancel_button = wx.Button(self, wx.ID_CANCEL, "Cancelar")

        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(self.search, 0, wx.ALL | wx.EXPAND, 12)
        layout.Add(self.section, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        layout.Add(self.results, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        layout.Add(self.load_more_button, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        layout.Add(self.status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self.favorite_button, 0, wx.RIGHT, 8)
        buttons.AddStretchSpacer()
        buttons.Add(self.insert_button, 0, wx.RIGHT, 8)
        buttons.Add(self.cancel_button)
        layout.Add(buttons, 0, wx.ALL | wx.EXPAND, 12)
        self.SetSizer(layout)
        self.SetSize((560, 470))
        self.SetMinSize((450, 330))

        self.search.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self.search.Bind(wx.EVT_TEXT_ENTER, self._on_accept)
        self.search.Bind(wx.EVT_KEY_DOWN, self._on_search_key)
        self.section.Bind(wx.EVT_CHOICE, self._on_filter_changed)
        self.results.Bind(wx.EVT_LISTBOX, self._on_selected)
        self.results.Bind(wx.EVT_LISTBOX_DCLICK, self._on_accept)
        self.results.Bind(wx.EVT_KEY_DOWN, self._on_results_key)
        self.load_more_button.Bind(wx.EVT_BUTTON, self._load_more)
        self.favorite_button.Bind(wx.EVT_BUTTON, self._on_favorite)
        self.insert_button.Bind(wx.EVT_BUTTON, self._on_accept)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self._refresh_results()
        self.search.SetFocus()

    def selected_emoji(self) -> str:
        index = self.results.GetSelection()
        return self._choices[index].value if 0 <= index < len(self._choices) else ""

    def remember_selection(self, value: str) -> None:
        """Called only after insertion succeeds; cancelling never changes recents."""
        if value not in self._by_value:
            return
        self.recent = [
            value, *(other for other in self.recent if other != value)
        ][:MAX_RECENT_EMOJIS]
        self.settings.save_emoji_preferences(self.favorites, self.recent)

    def _refresh_results(self) -> None:
        selected = self.selected_emoji()
        section = self._sections[self.section.GetSelection()]
        if section in ("favorites", "recent"):
            values = self.favorites if section == "favorites" else self.recent
            entries = tuple(self._by_value[value] for value in values)
        elif section == "all":
            entries = self._catalog
        else:
            entries = tuple(entry for entry in self._catalog if entry.group == section)
        self._choices = matching_emojis(self.search.GetValue(), entries)
        self.results.Freeze()
        try:
            self.results.Set([self._row_label(entry)
                              for entry in self._choices[:EMOJI_PAGE_SIZE]])
            if self._choices:
                index = next((i for i, entry in enumerate(self._choices[:EMOJI_PAGE_SIZE])
                              if entry.value == selected), 0)
                self.results.SetSelection(index)
        finally:
            self.results.Thaw()
        self._update_page_status()
        self._update_buttons()

    def _row_label(self, entry: EmojiEntry) -> str:
        return f"{entry.value} — {entry.label}" + (
            " — favorito" if entry.value in self.favorites else ""
        )

    def _update_page_status(self) -> None:
        shown, total = self.results.GetCount(), len(self._choices)
        self.status.SetLabel(
            f"Mostrando {shown} de {total} emojis" if shown < total
            else f"{total} emojis" if total else "No hay emojis en esta selección."
        )
        more = shown < total
        had_button_focus = self.load_more_button.HasFocus()
        self.load_more_button.Show(more)
        if had_button_focus and not more:
            self.results.SetFocus()
        self.Layout()

    def _load_more(self, _event: wx.Event | None = None) -> None:
        start = self.results.GetCount()
        batch = self._choices[start:start + EMOJI_PAGE_SIZE]
        if not batch:
            return
        # Append a bounded batch; never rebuild the rows already being read.
        self.results.Freeze()
        try:
            self.results.Append([self._row_label(entry) for entry in batch])
        finally:
            self.results.Thaw()
        self._update_page_status()

    def _update_buttons(self) -> None:
        value = self.selected_emoji()
        self.insert_button.Enable(bool(value))
        self.favorite_button.Enable(bool(value))
        self.favorite_button.SetLabel(
            "Quitar de favoritos" if value in self.favorites else "Añadir a favoritos"
        )

    def _on_filter_changed(self, _event: wx.CommandEvent) -> None:
        self._refresh_results()

    def _on_selected(self, _event: wx.CommandEvent) -> None:
        self._update_buttons()

    def _on_favorite(self, _event: wx.CommandEvent) -> None:
        value = self.selected_emoji()
        if not value:
            return
        previous = list(self.favorites)
        if value in self.favorites:
            self.favorites.remove(value)
        else:
            self.favorites.append(value)
        try:
            self.settings.save_emoji_preferences(self.favorites, self.recent)
        except OSError:
            self.favorites = previous
            wx.MessageBox("No se pudieron guardar los favoritos.", "Emojis", wx.OK | wx.ICON_ERROR)
        index = self.results.GetSelection()
        if (
            self._sections[self.section.GetSelection()] == "favorites"
            and value not in self.favorites
        ):
            # Removing one favorite must not rebuild the pages already loaded.
            self._choices.pop(index)
            self.results.Freeze()
            try:
                self.results.Delete(index)
                shown = self.results.GetCount()
                if shown < len(self._choices):
                    self.results.Append(self._row_label(self._choices[shown]))
                if self._choices:
                    self.results.SetSelection(min(index, self.results.GetCount() - 1))
            finally:
                self.results.Thaw()
            self._update_page_status()
        else:
            self.results.SetString(index, self._row_label(self._choices[index]))
        self._update_buttons()

    def _on_accept(self, _event: wx.Event) -> None:
        if self.selected_emoji():
            self.EndModal(wx.ID_OK)

    def _on_search_key(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() in (wx.WXK_DOWN, wx.WXK_UP) and self._choices:
            self.results.SetFocus()
            return
        event.Skip()

    def _on_results_key(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._on_accept(event)
            return
        if (
            event.GetKeyCode() == wx.WXK_DOWN
            and self.results.GetSelection() == self.results.GetCount() - 1
        ):
            self._load_more()
        event.Skip()

    def _on_key(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        if event.ControlDown() and not event.AltDown() and event.GetKeyCode() == ord("D"):
            self._on_favorite(event)
            return
        event.Skip()
