from __future__ import annotations

from collections.abc import Callable

import wx
import wx.adv


class SystemTrayIcon(wx.adv.TaskBarIcon):
    """Icono de la aplicacion y menu de restauracion/cierre."""

    def __init__(self, *, on_show: Callable[[], None], on_exit: Callable[[], None]) -> None:
        super().__init__()
        self._on_show_callback = on_show
        self._on_exit_callback = on_exit
        self.SetIcon(self._application_icon(), "WhatsApp CAN")
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DOWN, self._on_left_click)
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self._on_left_click)

    def CreatePopupMenu(self) -> wx.Menu:
        menu = wx.Menu()
        show_item = menu.Append(wx.ID_ANY, "Mostrar WhatsApp CAN")
        menu.AppendSeparator()
        exit_item = menu.Append(wx.ID_EXIT, "Salir")
        menu.Bind(wx.EVT_MENU, self._on_show, show_item)
        menu.Bind(wx.EVT_MENU, self._on_exit, exit_item)
        return menu

    @staticmethod
    def _application_icon() -> wx.Icon:
        icon = wx.ArtProvider.GetIcon(
            wx.ART_INFORMATION,
            wx.ART_OTHER,
            wx.Size(32, 32),
        )
        if icon.IsOk():
            return icon

        fallback = wx.Icon()
        return fallback

    def _on_left_click(self, _event: wx.Event) -> None:
        self._on_show_callback()

    def _on_show(self, _event: wx.CommandEvent) -> None:
        self._on_show_callback()

    def _on_exit(self, _event: wx.CommandEvent) -> None:
        self._on_exit_callback()
