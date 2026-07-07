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

    def set_chats(
        self,
        chats: list[Chat],
        selected_jid: str = "",
        preserve_focused_order: bool = True,
    ) -> None:
        selected_jid = selected_jid or self._selected_chat_jid()
        if preserve_focused_order and self.list_box.HasFocus() and self._chats:
            chats = self._preserve_current_order(chats)
            rows = [self._format_chat_row(chat) for chat in chats]
            self._chats = list(chats)
            self._sync_rows_incrementally(rows)
            if selected_jid:
                self.select_chat_by_jid(selected_jid)
            return

        previous_jids = [chat.jid for chat in self._chats]
        next_jids = [chat.jid for chat in chats]
        rows = [self._format_chat_row(chat) for chat in chats]

        if previous_jids == next_jids and self.list_box.GetCount() == len(rows):
            self._chats = list(chats)
            for index, row in enumerate(rows):
                if self.list_box.GetString(index) != row:
                    self.list_box.SetString(index, row)
            if selected_jid:
                self.select_chat_by_jid(selected_jid)
            return

        self._chats = list(chats)
        self.list_box.Set(rows)
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

    def _preserve_current_order(self, chats: list[Chat]) -> list[Chat]:
        chats_by_jid = {chat.jid: chat for chat in chats}
        ordered = [
            chats_by_jid.pop(current.jid)
            for current in self._chats
            if current.jid in chats_by_jid
        ]
        ordered.extend(chats_by_jid.values())
        return ordered

    def _sync_rows_incrementally(self, rows: list[str]) -> None:
        current_count = self.list_box.GetCount()
        common_count = min(current_count, len(rows))

        for index in range(common_count):
            if self.list_box.GetString(index) != rows[index]:
                self.list_box.SetString(index, rows[index])

        for index in range(current_count - 1, len(rows) - 1, -1):
            self.list_box.Delete(index)

        for row in rows[current_count:]:
            self.list_box.Append(row)

    def selected_chat(self) -> Chat | None:
        index = self.list_box.GetSelection()
        if index == wx.NOT_FOUND or index >= len(self._chats):
            return None
        return self._chats[index]

    def _selected_chat_jid(self) -> str:
        chat = self.selected_chat()
        return chat.jid if chat else ""

    def select_first(self) -> Chat | None:
        if not self._chats:
            return None

        if self.list_box.GetSelection() != 0:
            self.list_box.SetSelection(0)
        return self._chats[0]

    def select_chat_by_jid(self, jid: str) -> Chat | None:
        for index, chat in enumerate(self._chats):
            if chat.jid == jid:
                if self.list_box.GetSelection() != index:
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
        name = chat.name
        status = self._format_status(chat)
        preview = self._truncate_preview(chat.last_message_preview)
        time = self._format_time(chat.last_message_at)
        details = " | ".join(part for part in (status, preview, time) if part)
        if not details:
            return name

        return f"{name} | {details}"

    @staticmethod
    def _format_status(chat: Chat) -> str:
        if chat.unread_count <= 0:
            return ""

        if chat.unread_count == 1:
            return "1 mensaje no leído"

        return f"{chat.unread_count} mensajes no leídos"

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
