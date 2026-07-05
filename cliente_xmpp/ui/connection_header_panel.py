from __future__ import annotations

import wx


class ConnectionHeaderPanel(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)

        self.account_label = wx.StaticText(self, label="Cuenta:")
        self.status_label = wx.StaticText(self, label="Conectado")
        self.disconnect_button = wx.Button(self, label="Desconectar")

        self._layout()

    def set_account(self, jid: str) -> None:
        self.account_label.SetLabel(f"Cuenta: {jid}")

    def set_status(self, status: str) -> None:
        self.status_label.SetLabel(status)

    def _layout(self) -> None:
        box = wx.BoxSizer(wx.HORIZONTAL)
        box.Add(self.account_label, 1, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 10)
        box.Add(self.status_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 10)
        box.Add(self.disconnect_button, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 10)
        self.SetSizer(box)
