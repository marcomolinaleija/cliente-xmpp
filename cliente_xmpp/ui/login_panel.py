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

        self.settings = settings
        self.jid: wx.TextCtrl
        self.password: wx.TextCtrl
        self.host: wx.TextCtrl
        self.port: wx.SpinCtrl
        self.use_tls: wx.CheckBox
        self.connect_button: wx.Button

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
        box = wx.BoxSizer(wx.VERTICAL)

        box.Add(wx.StaticText(self, label="JID:"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.jid = wx.TextCtrl(self, value=self.settings.jid)
        self.jid.SetToolTip("Usuario XMPP completo, por ejemplo usuario@servidor.com.")
        box.Add(self.jid, 0, wx.ALL | wx.EXPAND, 10)

        box.Add(wx.StaticText(self, label="Password:"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.password = wx.TextCtrl(self, style=wx.TE_PASSWORD)
        self.password.SetToolTip("Password de la cuenta XMPP.")
        box.Add(self.password, 0, wx.ALL | wx.EXPAND, 10)

        box.Add(wx.StaticText(self, label="Servidor:"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.host = wx.TextCtrl(self, value=self.settings.host)
        self.host.SetToolTip("Servidor XMPP. Puedes dejarlo vacio si el JID resuelve el host.")
        box.Add(self.host, 0, wx.ALL | wx.EXPAND, 10)

        box.Add(wx.StaticText(self, label="Puerto:"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.port = wx.SpinCtrl(self, min=1, max=65535, initial=self.settings.port)
        self.port.SetToolTip("Puerto XMPP. Normalmente 5222.")
        box.Add(self.port, 0, wx.ALL | wx.EXPAND, 10)

        self.use_tls = wx.CheckBox(self, label="Exigir STARTTLS")
        self.use_tls.SetValue(self.settings.use_tls)
        self.use_tls.SetToolTip("Usar STARTTLS para cifrar la conexion XMPP.")
        box.Add(self.use_tls, 0, wx.ALL | wx.EXPAND, 10)

        self.connect_button = wx.Button(self, label="Conectar")
        box.Add(self.connect_button, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        self.SetSizer(box)
