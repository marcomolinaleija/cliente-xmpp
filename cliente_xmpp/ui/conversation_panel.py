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
        index = self.messages.GetItemCount()
        self.messages.InsertItem(index, self._format_message_row(message))
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
        self.messages.InsertColumn(0, "Mensajes", width=820)

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

    def _format_message_row(self, message: Message) -> str:
        timestamp = self._format_message_time(message)
        if message.outgoing:
            return f"Tu {message.body} {timestamp} Entregado."

        sender = self.resolve_display_name(message.sender_jid)
        return f"{sender} {message.body}, {timestamp}"

    def _format_message_time(self, message: Message) -> str:
        hour = message.sent_at.hour
        minute = message.sent_at.minute
        suffix = "a. m." if hour < 12 else "p. m."
        hour_12 = hour % 12 or 12
        return f"{hour_12}:{minute:02d} {suffix}"
