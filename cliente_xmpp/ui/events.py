from __future__ import annotations

import wx

from cliente_xmpp.xmpp.events import XmppEvent


wxEVT_XMPP_EVENT = wx.NewEventType()
EVT_XMPP_EVENT = wx.PyEventBinder(wxEVT_XMPP_EVENT, 1)


class WxXmppEvent(wx.PyCommandEvent):
    def __init__(self, event: XmppEvent) -> None:
        super().__init__(wxEVT_XMPP_EVENT)
        self.event = event

