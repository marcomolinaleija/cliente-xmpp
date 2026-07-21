from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import wx

from cliente_xmpp.models.chat import Chat, Message


@dataclass(slots=True)
class ChatListItem:
    chat: Chat
    message: Message | None = None

    @property
    def is_message_result(self) -> bool:
        return self.message is not None


class ChatListPanel(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self._chats: list[Chat] = []
        self._chat_indexes_by_jid: dict[str, int] = {}
        self._items: list[ChatListItem] = []
        self._last_selected_jid = ""
        self._updating = False
        self._searching = False
        self._visible_stale = False

        self.search_label = wx.StaticText(self, label="Buscar:")
        self.search_ctrl = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.search_ctrl.SetName("Buscar chats y mensajes")
        self.search_ctrl.SetToolTip("Buscar contactos, telefonos y mensajes.")
        self.search_ctrl.SetMinSize((280, -1))
        self.new_chat_button = wx.Button(self, label="&Nuevo chat")
        self.new_chat_button.SetToolTip(
            "Abre una conversación con un número de WhatsApp. Atajo: Control+N."
        )
        self.list_box = wx.ListBox(self)
        self.list_box.SetName("Lista de chats")
        self.list_box.SetToolTip(
            "Chats con actividad. Pulsa Enter para abrir el chat seleccionado."
        )
        self.chat_list_label = wx.StaticText(self, label="Chats")

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        toolbar.Add(self.chat_list_label, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 18)
        toolbar.AddStretchSpacer(1)
        toolbar.Add(self.search_label, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 8)
        toolbar.Add(self.search_ctrl, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 10)
        toolbar.Add(self.new_chat_button, 0, wx.ALIGN_CENTER_VERTICAL)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(toolbar, 0, wx.ALL | wx.EXPAND, 12)
        box.Add(self.list_box, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        self.SetSizer(box)

    @property
    def is_updating(self) -> bool:
        return self._updating

    @property
    def is_searching(self) -> bool:
        return self._searching

    def set_chats(
        self,
        chats: list[Chat],
        selected_jid: str = "",
        preserve_focused_order: bool = True,
    ) -> None:
        self._chats = list(chats)
        self._rebuild_chat_indexes()
        if self._searching:
            return
        if not self.IsShown() or (self.list_box.HasFocus() and self._items):
            self._visible_stale = True
            return

        self._set_items(
            [ChatListItem(chat=chat) for chat in chats],
            selected_jid=selected_jid,
            preserve_focused_order=preserve_focused_order,
        )

    def set_search_results(self, items: list[ChatListItem], selected_jid: str = "") -> None:
        self._searching = True
        self._set_items(items, selected_jid=selected_jid, preserve_focused_order=False)

    def set_placeholder(self, text: str) -> None:
        self._chats = []
        self._chat_indexes_by_jid = {}
        self._items = []
        self._last_selected_jid = ""
        self._visible_stale = False
        self._searching = False
        self.list_box.Set([text])

    def clear_search_results(self, selected_jid: str = "") -> None:
        self._searching = False
        self._set_items(
            [ChatListItem(chat=chat) for chat in self._chats],
            selected_jid=selected_jid,
            preserve_focused_order=False,
        )
        self._visible_stale = False

    def refresh_visible_if_stale(self) -> None:
        if not self._visible_stale:
            return

        self.force_refresh_visible()

    def force_refresh_visible(self, selected_jid: str = "") -> None:
        self._visible_stale = False
        self._set_items(
            [ChatListItem(chat=chat) for chat in self._chats],
            selected_jid=selected_jid,
            preserve_focused_order=False,
            force=True,
        )

    def _set_items(
        self,
        items: list[ChatListItem],
        selected_jid: str = "",
        preserve_focused_order: bool = True,
        force: bool = False,
    ) -> None:
        selected_jid = selected_jid or self._selected_chat_jid()
        if preserve_focused_order and self.list_box.HasFocus() and self._items:
            items = self._preserve_current_order(items)

        if not force and self.list_box.HasFocus() and self._items:
            self._sync_items_incrementally(items, selected_jid)
            return

        previous_keys = [self._item_key(item) for item in self._items]
        next_keys = [self._item_key(item) for item in items]
        rows = [self._format_item_row(item) for item in items]

        if previous_keys == next_keys and self.list_box.GetCount() == len(rows):
            self._items = list(items)
            for index, row in enumerate(rows):
                if self.list_box.GetString(index) != row:
                    self.list_box.SetString(index, row)
            if selected_jid:
                self.select_chat_by_jid(selected_jid)
            return

        self.list_box.Freeze()
        try:
            self._items = list(items)
            self.list_box.Set(rows)
            if selected_jid:
                self.select_chat_by_jid(selected_jid)
        finally:
            self.list_box.Thaw()

    def upsert_chat(self, chat: Chat) -> None:
        index = self._chat_indexes_by_jid.get(chat.jid)
        if index is not None:
            self._chats[index] = chat
            if not self.IsShown() or self.list_box.HasFocus():
                self._visible_stale = True
                return
            self._update_visible_chat(chat)
            return

        self._chat_indexes_by_jid[chat.jid] = len(self._chats)
        self._chats.append(chat)
        if not self.IsShown() or self.list_box.HasFocus():
            self._visible_stale = True
            return
        if not self._searching:
            item = ChatListItem(chat=chat)
            self._items.append(item)
            self.list_box.Append(self._format_item_row(item))

    def _update_visible_chat(self, chat: Chat) -> None:
        for index, item in enumerate(self._items):
            if item.chat.jid != chat.jid:
                continue

            self._items[index] = ChatListItem(chat=chat, message=item.message)
            self.list_box.SetString(index, self._format_item_row(self._items[index]))

    def _preserve_current_order(self, items: list[ChatListItem]) -> list[ChatListItem]:
        items_by_key = {self._item_key(item): item for item in items}
        ordered = [
            items_by_key.pop(self._item_key(current))
            for current in self._items
            if self._item_key(current) in items_by_key
        ]
        ordered.extend(items_by_key.values())
        return ordered

    def _sync_items_incrementally(
        self,
        items: list[ChatListItem],
        selected_jid: str = "",
    ) -> None:
        self._updating = True
        self.list_box.Freeze()
        try:
            for target_index, item in enumerate(items):
                row = self._format_item_row(item)
                current_index = self._item_index(self._item_key(item), start=target_index)
                if current_index == target_index:
                    self._items[target_index] = item
                    if self.list_box.GetString(target_index) != row:
                        self.list_box.SetString(target_index, row)
                    continue

                if current_index is not None:
                    self.list_box.Delete(current_index)
                    self._items.pop(current_index)

                self._items.insert(target_index, item)
                self.list_box.Insert(row, target_index)

            for index in range(len(self._items) - 1, len(items) - 1, -1):
                self.list_box.Delete(index)
                self._items.pop(index)

            if selected_jid:
                for index, item in enumerate(self._items):
                    if item.chat.jid == selected_jid:
                        self.list_box.SetSelection(index)
                        self._last_selected_jid = selected_jid
                        break
        finally:
            self.list_box.Thaw()
            self._updating = False

    def _item_index(self, key: tuple[object, ...], start: int = 0) -> int | None:
        for index in range(start, len(self._items)):
            if self._item_key(self._items[index]) == key:
                return index

        return None

    @staticmethod
    def _item_key(item: ChatListItem) -> tuple[object, ...]:
        if item.message is None:
            return "chat", item.chat.jid
        if item.message.message_id:
            return "message_id", item.chat.jid, item.message.message_id
        return (
            "message",
            item.chat.jid,
            item.message.sent_at.isoformat(),
            item.message.sender_jid,
            item.message.body,
            item.message.media_url,
        )

    def selected_chat(self) -> Chat | None:
        item = self.selected_item()
        if item is not None:
            self._last_selected_jid = item.chat.jid
            return item.chat

        return self._chat_by_jid(self._last_selected_jid)

    def selected_item(self) -> ChatListItem | None:
        index = self.list_box.GetSelection()
        if index != wx.NOT_FOUND and index < len(self._items):
            return self._items[index]

        return None

    def _selected_chat_jid(self) -> str:
        chat = self.selected_chat()
        return chat.jid if chat else self._last_selected_jid

    def select_first(self) -> Chat | None:
        if not self._items:
            return None

        if self.list_box.GetSelection() != 0:
            self.list_box.SetSelection(0)
        self._last_selected_jid = self._items[0].chat.jid
        return self._items[0].chat

    def select_chat_by_jid(self, jid: str) -> Chat | None:
        for index, item in enumerate(self._items):
            if item.chat.jid == jid:
                if self.list_box.GetSelection() != index:
                    self.list_box.SetSelection(index)
                self._last_selected_jid = jid
                return item.chat

        return None

    def _chat_by_jid(self, jid: str) -> Chat | None:
        if not jid:
            return None

        index = self._chat_indexes_by_jid.get(jid)
        if index is not None and index < len(self._chats):
            return self._chats[index]

        return None

    def focus(self) -> None:
        self.list_box.SetFocus()

    def focus_search(self) -> None:
        self.search_ctrl.SetFocus()
        self.search_ctrl.SelectAll()

    def set_new_chat_enabled(self, enabled: bool) -> None:
        self.new_chat_button.Enable(enabled)

    def has_chat(self, jid: str) -> bool:
        return jid in self._chat_indexes_by_jid

    def chat_by_jid(self, jid: str) -> Chat | None:
        return self._chat_by_jid(jid)

    def chats(self) -> list[Chat]:
        return list(self._chats)

    def _rebuild_chat_indexes(self) -> None:
        self._chat_indexes_by_jid = {
            chat.jid: index for index, chat in enumerate(self._chats)
        }

    def _format_item_row(self, item: ChatListItem) -> str:
        if item.message is not None:
            return self._format_message_result_row(item.chat, item.message)

        return self._format_chat_row(item.chat)

    def _format_chat_row(self, chat: Chat) -> str:
        name = chat.name
        status = self._format_status(chat)
        preview = self._format_preview(chat.last_message_preview)
        time = self._format_time(chat.last_message_at)
        details = " | ".join(part for part in (status, preview, time) if part)
        if not details:
            return name

        return f"{name} | {details}"

    def _format_message_result_row(self, chat: Chat, message: Message) -> str:
        sender = (
            "Tú"
            if message.outgoing
            else self._sender_label(chat, message.sender_jid, message.sender_name)
        )
        preview = self._truncate_preview(message.body or message.media_filename or "Adjunto")
        time = self._format_time(message.sent_at)
        details = " | ".join(part for part in (sender, preview, time) if part)
        return f"{chat.name} | mensaje | {details}"

    @staticmethod
    def _sender_label(chat: Chat, sender_jid: str, sender_name: str = "") -> str:
        if not chat.is_group:
            return chat.name
        if sender_name:
            return sender_name
        if "/" in sender_jid:
            return sender_jid.rsplit("/", 1)[-1]
        return sender_jid or chat.name

    @staticmethod
    def _format_status(chat: Chat) -> str:
        parts: list[str] = []
        if chat.is_self_group:
            parts.append("grupo personal")
        if chat.notifications_muted:
            parts.append("silenciado")
        if chat.unread_count <= 0:
            return ", ".join(parts)

        if chat.unread_count == 1:
            parts.append("1 mensaje no leído")
            return ", ".join(parts)

        parts.append(f"{chat.unread_count} mensajes no leídos")
        return ", ".join(parts)

    @classmethod
    def _format_preview(cls, preview: str) -> str:
        preview = cls._truncate_preview(preview)
        for prefix, label in (
            ("Enviando: ", "Enviando"),
            ("No enviado: ", "No enviado"),
            ("Entregado: ", "Entregado"),
            ("LeÃ­do: ", "LeÃ­do"),
            ("Leído: ", "Leído"),
        ):
            if preview.startswith(prefix):
                body = preview.removeprefix(prefix).strip()
                return " | ".join(part for part in (body, label) if part)

        return preview

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

        if sent_at.tzinfo is not None:
            sent_at = sent_at.astimezone()

        hour = sent_at.hour
        minute = sent_at.minute
        suffix = "a. m." if hour < 12 else "p. m."
        hour_12 = hour % 12 or 12
        return f"{hour_12}:{minute:02d} {suffix}"
