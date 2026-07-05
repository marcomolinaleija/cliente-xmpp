from __future__ import annotations

import wx

from cliente_xmpp.models.chat import Chat, Message


class ConversationPanel(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self.current_chat: Chat | None = None

        self.title = wx.StaticText(self, label="Selecciona un chat")
        self.messages = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_NONE)
        self.compose = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        self.send_button = wx.Button(self, label="Enviar")
        self.send_button.Enable(False)

        self._layout()

    def set_chat(self, chat: Chat) -> None:
        self.current_chat = chat
        self.title.SetLabel(chat.name)
        self.messages.Clear()
        self.send_button.Enable(True)

    def append_message(self, message: Message) -> None:
        prefix = "Yo" if message.outgoing else message.sender_jid
        timestamp = message.sent_at.strftime("%H:%M")
        self.messages.AppendText(f"[{timestamp}] {prefix}: {message.body}\n")

    def consume_composed_message(self) -> str:
        body = self.compose.GetValue().strip()
        if body:
            self.compose.Clear()
        return body

    def _layout(self) -> None:
        composer = wx.BoxSizer(wx.HORIZONTAL)
        composer.Add(self.compose, 1, wx.EXPAND | wx.RIGHT, 8)
        composer.Add(self.send_button, 0, wx.EXPAND)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.title, 0, wx.ALL, 12)
        box.Add(self.messages, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)
        box.Add(composer, 0, wx.ALL | wx.EXPAND, 12)
        self.SetSizer(box)

