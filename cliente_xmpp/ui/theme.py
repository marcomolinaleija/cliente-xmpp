from __future__ import annotations

import wx

NAVY_BLUE = wx.Colour(4, 24, 66)
DARKER_BLUE = wx.Colour(2, 15, 42)
YELLOW = wx.Colour(255, 231, 92)
SELECTION_BLUE = wx.Colour(9, 48, 118)


def apply_theme(window: wx.Window) -> None:
    _apply_theme_to_window(window)
    for child in window.GetChildren():
        apply_theme(child)
    window.Refresh()


def _apply_theme_to_window(window: wx.Window) -> None:
    background = (
        DARKER_BLUE
        if isinstance(window, (wx.TextCtrl, wx.ListBox, wx.ListCtrl))
        else NAVY_BLUE
    )
    try:
        window.SetBackgroundColour(background)
    except Exception:
        pass

    try:
        window.SetForegroundColour(YELLOW)
    except Exception:
        pass

    if isinstance(window, wx.ListCtrl):
        window.SetTextColour(YELLOW)
        window.SetBackgroundColour(DARKER_BLUE)

    if isinstance(window, wx.TextCtrl):
        window.SetDefaultStyle(wx.TextAttr(YELLOW, DARKER_BLUE))
