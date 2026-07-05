from __future__ import annotations

from datetime import datetime

import wx

from cliente_xmpp.models.chat import Chat


class ChatListPanel(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self.list_box = wx.ListBox(self)
        self.open_button = wx.Button(self, label="Abrir")
        self._chats: list[Chat] = []

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.list_box, 1, wx.EXPAND)
        box.Add(self.open_button, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        self.SetSizer(box)

    def set_chats(self, chats: list[Chat], selected_jid: str = "") -> None:
        self._chats = list(chats)
        self.list_box.Set([self._format_chat_row(chat) for chat in chats])
        if selected_jid:
            self.select_chat_by_jid(selected_jid)

    def upsert_chat(self, chat: Chat) -> None:
        for index, current in enumerate(self._chats):
            if current.jid == chat.jid:
                self._chats[index] = chat
                self.list_box.SetString(index, self._format_chat_row(chat))
                return

        self._chats.append(chat)
        self.list_box.Append(self._format_chat_row(chat))

    def selected_chat(self) -> Chat | None:
        index = self.list_box.GetSelection()
        if index == wx.NOT_FOUND:
            return None
        return self._chats[index]

    def select_first(self) -> Chat | None:
        if not self._chats:
            return None

        self.list_box.SetSelection(0)
        return self._chats[0]

    def select_chat_by_jid(self, jid: str) -> Chat | None:
        for index, chat in enumerate(self._chats):
            if chat.jid == jid:
                self.list_box.SetSelection(index)
                return chat

        return None

    def focus(self) -> None:
        self.list_box.SetFocus()

    def has_chat(self, jid: str) -> bool:
        return any(chat.jid == jid for chat in self._chats)

    def chats(self) -> list[Chat]:
        return list(self._chats)

    def _format_chat_row(self, chat: Chat) -> str:
        status = self._format_status(chat)
        preview = self._truncate_preview(chat.last_message_preview)
        time = self._format_time(chat.last_message_at)
        details = " | ".join(part for part in (status, preview, time) if part)
        if not details:
            return chat.name

        return f"{chat.name} | {details}"

    @staticmethod
    def _format_status(chat: Chat) -> str:
        if chat.unread_count <= 0:
            return "Leido"

        if chat.unread_count == 1:
            return "No leido"

        return f"No leidos ({chat.unread_count})"

    @staticmethod
    def _truncate_preview(preview: str, max_length: int = 200) -> str:
        preview = " ".join(preview.split())
        if len(preview) <= max_length:
            return preview

        return f"{preview[: max_length - 3]}..."

    @staticmethod
    def _format_time(sent_at: datetime | None) -> str:
        if sent_at is None:
            return ""

        hour = sent_at.hour
        minute = sent_at.minute
        suffix = "a. m." if hour < 12 else "p. m."
        hour_12 = hour % 12 or 12
        return f"{hour_12}:{minute:02d} {suffix}"
