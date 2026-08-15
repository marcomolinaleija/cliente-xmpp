from __future__ import annotations

import wx

from cliente_xmpp.models.reactions import is_supported_reaction

REACTION_CHOICES: tuple[tuple[str, str], ...] = (
    ("👍", "me gusta pulgar arriba"),
    ("👎", "no me gusta pulgar abajo"),
    ("❤️", "corazón amor"),
    ("🔥", "fuego"),
    ("😂", "risa llorando"),
    ("😄", "sonrisa feliz"),
    ("😊", "sonrisa"),
    ("😍", "enamorado ojos corazón"),
    ("🥰", "cariño"),
    ("😘", "beso"),
    ("😮", "sorpresa asombro"),
    ("😢", "triste llanto"),
    ("😭", "llorando"),
    ("😡", "enojo"),
    ("🤔", "pensando duda"),
    ("🙄", "ojos mirada"),
    ("😱", "miedo grito"),
    ("🙏", "gracias manos"),
    ("👏", "aplauso"),
    ("💪", "fuerza"),
    ("🎉", "fiesta celebración"),
    ("✅", "correcto listo"),
    ("❌", "incorrecto no"),
    ("💯", "cien perfecto"),
    ("👀", "mirando ojos"),
    ("🤝", "acuerdo trato"),
    ("🤗", "abrazo"),
    ("🤩", "emocionado estrellas"),
    ("😴", "sueño dormido"),
    ("🤯", "mente explotando"),
    ("🫡", "saludo respeto"),
    ("💔", "corazón roto"),
    ("🚀", "cohete"),
    ("💡", "idea foco"),
    ("⭐", "estrella"),
)


def matching_reactions(query: str) -> list[tuple[str, str]]:
    query = query.strip().casefold()
    matches = [
        choice
        for choice in REACTION_CHOICES
        if not query or query in choice[0] or query in choice[1]
    ]
    if is_supported_reaction(query) and all(choice[0] != query for choice in matches):
        matches.insert(0, (query, "usar este emoji"))
    return matches


class EmojiReactionDialog(wx.Dialog):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(
            parent,
            title="Más reacciones",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._choices: list[tuple[str, str]] = []
        self.search = wx.SearchCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.search.SetName("Buscar o escribir una reacción")
        self.search.SetDescriptiveText("Busca por nombre o escribe un emoji")
        self.results = wx.ListBox(self, style=wx.LB_SINGLE)
        self.results.SetName("Resultados de reacciones")
        self.ok_button = wx.Button(self, wx.ID_OK, "Reaccionar")
        cancel_button = wx.Button(self, wx.ID_CANCEL, "Cancelar")
        self.ok_button.SetDefault()

        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(
            wx.StaticText(
                self,
                label="Busca una reacción o escribe un emoji compatible con WhatsApp.",
            ),
            0,
            wx.ALL,
            12,
        )
        layout.Add(self.search, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        layout.Add(self.results, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer()
        buttons.Add(self.ok_button, 0, wx.RIGHT, 8)
        buttons.Add(cancel_button)
        layout.Add(buttons, 0, wx.ALL | wx.EXPAND, 12)
        self.SetSizerAndFit(layout)
        self.SetMinSize((390, 300))

        self.search.Bind(wx.EVT_TEXT, self._on_search_changed)
        self.search.Bind(wx.EVT_TEXT_ENTER, self._on_search_enter)
        self.results.Bind(wx.EVT_LISTBOX_DCLICK, self._on_result_activated)
        self.results.Bind(wx.EVT_LISTBOX, self._on_result_selected)
        self.Bind(wx.EVT_BUTTON, self._on_accept, self.ok_button)
        self._refresh_results()
        self.search.SetFocus()

    def selected_reaction(self) -> str:
        selection = self.results.GetSelection()
        if selection == wx.NOT_FOUND or selection >= len(self._choices):
            return ""
        return self._choices[selection][0]

    def _refresh_results(self) -> None:
        self._choices = matching_reactions(self.search.GetValue())
        self.results.Set([f"{emoji} — {description}" for emoji, description in self._choices])
        if self._choices:
            self.results.SetSelection(0)
        self.ok_button.Enable(bool(self._choices))

    def _on_search_changed(self, _event: wx.CommandEvent) -> None:
        self._refresh_results()

    def _on_search_enter(self, _event: wx.CommandEvent) -> None:
        if self.selected_reaction():
            self.EndModal(wx.ID_OK)

    def _on_result_selected(self, _event: wx.CommandEvent) -> None:
        self.ok_button.Enable(bool(self.selected_reaction()))

    def _on_result_activated(self, _event: wx.CommandEvent) -> None:
        self.EndModal(wx.ID_OK)

    def _on_accept(self, _event: wx.CommandEvent) -> None:
        if self.selected_reaction():
            self.EndModal(wx.ID_OK)
