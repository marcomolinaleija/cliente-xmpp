from __future__ import annotations

import wx

from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.updates import start_startup_update_check


class ClienteXmppApp(wx.App):
    def OnInit(self) -> bool:
        self.SetAppName("whatsapp-CAN")
        self.SetAppDisplayName("WhatsApp CAN")
        self.SetVendorName("Marco ML")
        window = MainWindow()
        window.Show()
        wx.CallLater(2000, start_startup_update_check, window)
        return True


def main() -> None:
    app = ClienteXmppApp(False)
    app.MainLoop()


if __name__ == "__main__":
    main()
