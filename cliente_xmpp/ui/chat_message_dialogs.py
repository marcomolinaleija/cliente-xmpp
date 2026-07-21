from __future__ import annotations

from collections.abc import Callable

import wx

from cliente_xmpp.media.downloads import has_media, local_media_path, media_description
from cliente_xmpp.media.links import is_link_preview, message_links
from cliente_xmpp.models.chat import Message
from cliente_xmpp.ui.theme import apply_theme

MessagesLoadedCallback = Callable[[list[Message], str], None]
MessagesLoader = Callable[[MessagesLoadedCallback], None]
MessageAction = Callable[[Message], None]


def _message_description(message: Message) -> str:
    if has_media(message):
        description = media_description(message)
    elif message_links(message):
        description = message_links(message)[0].url
    else:
        description = message.body
    description = " ".join(description.split()) or "Mensaje sin texto"
    return description if len(description) <= 260 else f"{description[:257]}..."


def _message_datetime(message: Message) -> str:
    try:
        return message.sent_at.astimezone().strftime("%d/%m/%Y, %H:%M")
    except (AttributeError, OSError, ValueError):
        return "sin fecha"


class StarredMessagesDialog(wx.Dialog):
    def __init__(
        self,
        parent: wx.Window,
        chat_name: str,
        loader: MessagesLoader,
    ) -> None:
        super().__init__(
            parent,
            title=f"Mensajes destacados de {chat_name}",
            size=(820, 520),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._loader = loader
        self._active = True
        self._messages: list[Message] = []
        self.selected_message: Message | None = None

        self.status = wx.StaticText(self, label="Cargando mensajes destacados locales...")
        self.messages = wx.ListCtrl(
            self,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
        )
        self.messages.SetName("Mensajes destacados")
        self.messages.InsertColumn(0, "Mensaje", width=560)
        self.messages.InsertColumn(1, "Fecha", width=190)
        self.go_button = wx.Button(self, label="Ir al mensaje")
        close_button = wx.Button(self, wx.ID_CLOSE, "Cerrar")

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self.go_button, 0, wx.RIGHT, 8)
        buttons.Add(close_button, 0)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.status, 0, wx.ALL | wx.EXPAND, 12)
        box.Add(self.messages, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)
        box.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizer(box)
        self.SetMinSize((620, 380))

        self.go_button.Bind(wx.EVT_BUTTON, self._on_go_to_message)
        self.messages.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_go_to_message)
        self.Bind(wx.EVT_BUTTON, lambda _event: self.EndModal(wx.ID_CLOSE), close_button)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)
        apply_theme(self)
        wx.CallAfter(self.refresh)

    def deactivate(self) -> None:
        self._active = False

    def refresh(self) -> None:
        if not self._active:
            return
        self.go_button.Enable(False)

        def loaded(messages: list[Message], error: str) -> None:
            if not self._active:
                return
            self._finish_load(messages, error)

        self._loader(loaded)

    def _finish_load(self, messages: list[Message], error: str) -> None:
        if not self._active:
            return
        self._messages = messages
        self.messages.Freeze()
        try:
            self.messages.DeleteAllItems()
            for message in messages:
                index = self.messages.InsertItem(
                    self.messages.GetItemCount(),
                    _message_description(message),
                )
                self.messages.SetItem(index, 1, _message_datetime(message))
            if messages:
                self.messages.Select(0)
        finally:
            self.messages.Thaw()

        if error:
            self.status.SetLabel(error)
        elif messages:
            self.status.SetLabel(f"{len(messages)} mensajes destacados guardados localmente.")
        else:
            self.status.SetLabel("No hay mensajes destacados guardados para este chat.")
        self.go_button.Enable(bool(messages) and not error)

    def _on_go_to_message(self, _event: wx.Event) -> None:
        index = self.messages.GetFirstSelected()
        if index == wx.NOT_FOUND or index >= len(self._messages):
            return
        self.selected_message = self._messages[index]
        self.EndModal(wx.ID_OK)

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()


class ChatFilesDialog(wx.Dialog):
    _TABS: tuple[tuple[str, Callable[[Message], bool]], ...] = (
        ("Todos", lambda _message: True),
        (
            "Archivos",
            lambda message: has_media(message)
            and message.media_kind == "file"
            and not is_link_preview(message),
        ),
        ("Enlaces", lambda message: bool(message_links(message))),
        ("Fotos", lambda message: message.media_kind == "image" and not message.is_sticker),
        ("Videos", lambda message: message.media_kind == "video"),
        ("Audios", lambda message: message.media_kind == "audio"),
        ("Stickers", lambda message: message.is_sticker),
    )

    def __init__(
        self,
        parent: wx.Window,
        chat_name: str,
        loader: MessagesLoader,
        on_open: MessageAction,
        on_copy: MessageAction,
        on_delete: MessageAction,
    ) -> None:
        super().__init__(
            parent,
            title=f"Archivos y enlaces de {chat_name}",
            size=(900, 600),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._loader = loader
        self._on_open = on_open
        self._on_copy = on_copy
        self._on_delete = on_delete
        self._active = True
        self._messages: list[Message] = []
        self._messages_by_list: dict[int, list[Message]] = {}

        self.status = wx.StaticText(self, label="Cargando archivos y enlaces locales...")
        self.notebook = wx.Notebook(self)
        self.notebook.SetName("Tipos de archivos y enlaces")
        for label, _predicate in self._TABS:
            self._create_page(label)
        close_button = wx.Button(self, wx.ID_CLOSE, "Cerrar")

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.status, 0, wx.ALL | wx.EXPAND, 12)
        box.Add(self.notebook, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)
        box.Add(close_button, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizer(box)
        self.SetMinSize((720, 460))

        self.Bind(wx.EVT_BUTTON, lambda _event: self.EndModal(wx.ID_CLOSE), close_button)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)
        apply_theme(self)
        wx.CallAfter(self.refresh)

    def deactivate(self) -> None:
        self._active = False

    def refresh(self) -> None:
        if not self._active:
            return

        def loaded(messages: list[Message], error: str) -> None:
            if not self._active:
                return
            self._finish_load(messages, error)

        self._loader(loaded)

    def _create_page(self, label: str) -> None:
        page = wx.Panel(self.notebook)
        messages = wx.ListCtrl(
            page,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
        )
        messages.SetName(f"{label} de este chat")
        messages.InsertColumn(0, "Elemento", width=600)
        messages.InsertColumn(1, "Fecha", width=190)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(messages, 1, wx.ALL | wx.EXPAND, 10)
        page.SetSizer(box)
        self.notebook.AddPage(page, label)
        self._messages_by_list[id(messages)] = []
        messages.Bind(
            wx.EVT_LIST_ITEM_ACTIVATED,
            lambda _event, control=messages: self._open(control),
        )
        messages.Bind(
            wx.EVT_CONTEXT_MENU,
            lambda _event, control=messages: self._show_menu(control),
        )
        messages.Bind(
            wx.EVT_LIST_ITEM_RIGHT_CLICK,
            lambda event, control=messages: self._on_right_click(event, control),
        )

    def _finish_load(self, messages: list[Message], error: str) -> None:
        self._messages = messages
        for page_index, (_label, predicate) in enumerate(self._TABS):
            control = self.notebook.GetPage(page_index).GetChildren()[0]
            visible_messages = [message for message in messages if predicate(message)]
            self._messages_by_list[id(control)] = visible_messages
            control.Freeze()
            try:
                control.DeleteAllItems()
                for message in visible_messages:
                    index = control.InsertItem(
                        control.GetItemCount(),
                        _message_description(message),
                    )
                    control.SetItem(index, 1, _message_datetime(message))
                if visible_messages:
                    control.Select(0)
            finally:
                control.Thaw()

        if error:
            self.status.SetLabel(error)
        else:
            self.status.SetLabel(f"{len(messages)} archivos o enlaces guardados localmente.")

    def _on_right_click(self, event: wx.ListEvent, control: wx.ListCtrl) -> None:
        control.Select(event.GetIndex())
        self._show_menu(control)

    def _selected_message(self, control: wx.ListCtrl) -> Message | None:
        index = control.GetFirstSelected()
        messages = self._messages_by_list.get(id(control), [])
        if index == wx.NOT_FOUND or index >= len(messages):
            return None
        return messages[index]

    def _show_menu(self, control: wx.ListCtrl) -> None:
        message = self._selected_message(control)
        if message is None:
            return
        menu = wx.Menu()
        open_item = menu.Append(wx.ID_ANY, "Abrir")
        copy_item = menu.Append(wx.ID_ANY, "Copiar")
        delete_item = menu.Append(wx.ID_ANY, "Eliminar")
        copy_item.Enable(local_media_path(message) is not None or bool(message_links(message)))
        delete_item.Enable(local_media_path(message) is not None)
        self.Bind(wx.EVT_MENU, lambda _event: self._open(control), open_item)
        self.Bind(wx.EVT_MENU, lambda _event: self._copy(control), copy_item)
        self.Bind(wx.EVT_MENU, lambda _event: self._delete(control), delete_item)
        self.PopupMenu(menu)
        menu.Destroy()

    def _open(self, control: wx.ListCtrl) -> None:
        message = self._selected_message(control)
        if message is not None:
            self._on_open(message)

    def _copy(self, control: wx.ListCtrl) -> None:
        message = self._selected_message(control)
        if message is not None:
            self._on_copy(message)

    def _delete(self, control: wx.ListCtrl) -> None:
        message = self._selected_message(control)
        if message is not None:
            self._on_delete(message)
            self._finish_load(self._messages, "")

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()
