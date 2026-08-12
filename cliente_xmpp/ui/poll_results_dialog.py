from __future__ import annotations

import wx

from cliente_xmpp.ui.theme import apply_theme


class PollResultsDialog(wx.Dialog):
    """Accessible, scrollable presentation of the latest known poll results."""

    def __init__(self, parent: wx.Window, title: str, results: str) -> None:
        super().__init__(
            parent,
            title="Resultados de encuesta",
            size=(620, 500),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        heading = wx.StaticText(self, label=title)
        self.results = wx.TextCtrl(
            self,
            value=results,
            style=wx.TE_MULTILINE | wx.TE_READONLY,
        )
        self.results.SetName(f"Resultados de la encuesta {title}")
        close_button = wx.Button(self, wx.ID_CLOSE, "&Cerrar")

        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(heading, 0, wx.ALL | wx.EXPAND, 12)
        layout.Add(self.results, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)
        layout.Add(close_button, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizer(layout)
        self.SetMinSize((460, 340))

        self.Bind(wx.EVT_BUTTON, lambda _event: self.EndModal(wx.ID_CLOSE), close_button)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)
        apply_theme(self)
        self.CentreOnParent()
        wx.CallAfter(self.results.SetFocus)

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()
