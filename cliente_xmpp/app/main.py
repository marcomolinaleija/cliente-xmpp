from __future__ import annotations

import wx

from cliente_xmpp.app.single_instance import SingleInstanceGuard
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.updates import start_startup_update_check


class ClienteXmppApp(wx.App):
    def __init__(self, single_instance: SingleInstanceGuard) -> None:
        super().__init__(False)
        self._single_instance = single_instance
        self._main_window: MainWindow | None = None
        self._activation_timer: wx.Timer | None = None

    def OnInit(self) -> bool:
        self.SetAppName("whatsapp-CAN")
        self.SetAppDisplayName("WhatsApp CAN")
        self.SetVendorName("Marco ML")
        window = MainWindow()
        self._main_window = window
        window.Show()
        self._activation_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_activation_timer, self._activation_timer)
        self._activation_timer.Start(250)
        wx.CallLater(2000, start_startup_update_check, window)
        return True

    def _on_activation_timer(self, _event: wx.TimerEvent) -> None:
        if self._single_instance.consume_activation_request() and self._main_window is not None:
            self._main_window.show_from_second_instance()

    def OnExit(self) -> int:
        if self._activation_timer is not None:
            self._activation_timer.Stop()
        return 0


def main() -> None:
    single_instance = SingleInstanceGuard()
    if not single_instance.acquire():
        single_instance.request_activation()
        single_instance.close()
        return

    try:
        app = ClienteXmppApp(single_instance)
        app.MainLoop()
    finally:
        single_instance.close()


if __name__ == "__main__":
    main()
