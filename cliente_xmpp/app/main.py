from __future__ import annotations

import wx

from cliente_xmpp.ui.main_window import MainWindow


class ClienteXmppApp(wx.App):
    def OnInit(self) -> bool:
        self.SetAppName("whatsapp-CAN")
        window = MainWindow()
        window.Show()
        return True


def main() -> None:
    app = ClienteXmppApp(False)
    app.MainLoop()


if __name__ == "__main__":
    main()
