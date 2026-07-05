from __future__ import annotations

from dataclasses import dataclass

import wx

from cliente_xmpp.config.settings import ConnectionSettings


@dataclass(slots=True)
class LoginData:
    settings: ConnectionSettings
    password: str


class LoginPanel(wx.Panel):
    def __init__(self, parent: wx.Window, settings: ConnectionSettings) -> None:
        super().__init__(parent)

        self.jid = wx.TextCtrl(self, value=settings.jid)
        self.password = wx.TextCtrl(self, style=wx.TE_PASSWORD)
        self.host = wx.TextCtrl(self, value=settings.host)
        self.port = wx.SpinCtrl(self, min=1, max=65535, initial=settings.port)
        self.use_tls = wx.CheckBox(self, label="TLS")
        self.use_tls.SetValue(settings.use_tls)
        self.connect_button = wx.Button(self, label="Conectar")

        self._layout()

    def get_login_data(self) -> LoginData:
        settings = ConnectionSettings(
            jid=self.jid.GetValue().strip(),
            host=self.host.GetValue().strip(),
            port=self.port.GetValue(),
            use_tls=self.use_tls.GetValue(),
        )
        return LoginData(settings=settings, password=self.password.GetValue())

    def set_connecting(self, connecting: bool) -> None:
        self.connect_button.Enable(not connecting)
        self.connect_button.SetLabel("Conectando..." if connecting else "Conectar")

    def _layout(self) -> None:
        form = wx.FlexGridSizer(rows=0, cols=2, vgap=8, hgap=8)
        form.AddGrowableCol(1)
        form.Add(wx.StaticText(self, label="JID"), 0, wx.ALIGN_CENTER_VERTICAL)
        form.Add(self.jid, 1, wx.EXPAND)
        form.Add(wx.StaticText(self, label="Password"), 0, wx.ALIGN_CENTER_VERTICAL)
        form.Add(self.password, 1, wx.EXPAND)
        form.Add(wx.StaticText(self, label="Servidor"), 0, wx.ALIGN_CENTER_VERTICAL)
        form.Add(self.host, 1, wx.EXPAND)
        form.Add(wx.StaticText(self, label="Puerto"), 0, wx.ALIGN_CENTER_VERTICAL)
        form.Add(self.port, 0, wx.EXPAND)
        form.AddSpacer(1)
        form.Add(self.use_tls, 0, wx.ALIGN_CENTER_VERTICAL)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(form, 0, wx.ALL | wx.EXPAND, 16)
        box.Add(self.connect_button, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.ALIGN_RIGHT, 16)
        self.SetSizer(box)

