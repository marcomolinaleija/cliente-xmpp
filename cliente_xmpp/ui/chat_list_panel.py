from __future__ import annotations

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
        self.list_box.Set([chat.name for chat in chats])
        if selected_jid:
            self.select_chat_by_jid(selected_jid)

    def upsert_chat(self, chat: Chat) -> None:
        for index, current in enumerate(self._chats):
            if current.jid == chat.jid:
                self._chats[index] = chat
                self.list_box.SetString(index, chat.name)
                return

        self._chats.append(chat)
        self.list_box.Append(chat.name)

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
