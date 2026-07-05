from __future__ import annotations

import wx
from collections.abc import Callable

from cliente_xmpp.models.chat import Chat, Message


class ConversationPanel(wx.Panel):
    def __init__(self, parent: wx.Window, resolve_display_name: Callable[[str], str]) -> None:
        super().__init__(parent)
        self.resolve_display_name = resolve_display_name
        self.current_chat: Chat | None = None

        self.title = wx.StaticText(self, label="Selecciona un chat")
        self.back_button = wx.Button(self, label="Volver")
        self.messages = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_NONE)
        self.compose: wx.TextCtrl
        self.send_button: wx.Button

        self._layout()

    def set_chat(self, chat: Chat) -> None:
        self.current_chat = chat
        self.title.SetLabel(chat.name)
        self.messages.DeleteAllItems()
        self.send_button.Enable(True)

    def append_message(self, message: Message) -> None:
        prefix = "Yo" if message.outgoing else self.resolve_display_name(message.sender_jid)
        timestamp = message.sent_at.strftime("%H:%M")
        index = self.messages.GetItemCount()
        self.messages.InsertItem(index, prefix)
        self.messages.SetItem(index, 1, message.body)
        self.messages.SetItem(index, 2, timestamp)
        self.messages.EnsureVisible(index)

    def focus_composer(self) -> None:
        self.compose.SetFocus()

    def consume_composed_message(self) -> str:
        body = self.compose.GetValue().strip()
        if body:
            self.compose.Clear()
        return body

    def _layout(self) -> None:
        header = wx.BoxSizer(wx.HORIZONTAL)
        header.Add(self.title, 1, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 12)
        header.Add(self.back_button, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 12)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(header, 0, wx.EXPAND)
        box.Add(self.messages, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)
        self.messages.InsertColumn(0, "Usuario", width=180)
        self.messages.InsertColumn(1, "Mensaje", width=520)
        self.messages.InsertColumn(2, "Hora", width=90)

        box.Add(wx.StaticText(self, label="Mensaje:"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)

        composer = wx.BoxSizer(wx.HORIZONTAL)
        self.compose = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        self.compose.SetToolTip("Escribe el mensaje para el chat seleccionado.")
        composer.Add(self.compose, 1, wx.EXPAND | wx.RIGHT, 8)

        self.send_button = wx.Button(self, label="Enviar")
        self.send_button.Enable(False)
        composer.Add(self.send_button, 0, wx.EXPAND)

        box.Add(composer, 0, wx.ALL | wx.EXPAND, 12)
        self.SetSizer(box)
