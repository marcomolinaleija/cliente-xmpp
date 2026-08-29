from __future__ import annotations

import calendar
from collections.abc import Callable
from datetime import date

import wx

from cliente_xmpp.media.downloads import (
    can_describe_with_rayoai,
    has_media,
    local_media_path,
    media_description,
)
from cliente_xmpp.media.links import is_link_preview, message_links
from cliente_xmpp.models.chat import Message
from cliente_xmpp.ui.theme import apply_theme

MessagesLoadedCallback = Callable[[list[Message], str], None]
MessagesLoader = Callable[[MessagesLoadedCallback], None]
MessageAction = Callable[[Message], None]
MessageDescribeAction = Callable[[Message], Message | None]
MessageSearchLoader = Callable[[str, date | None, MessagesLoadedCallback], None]
MessageDatesLoadedCallback = Callable[[list[date], str], None]
MessageDatesLoader = Callable[[MessageDatesLoadedCallback], None]
MessageKeyAction = Callable[[Message], bool]


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


def _message_sort_timestamp(message: Message) -> float:
    try:
        return message.sent_at.timestamp()
    except (AttributeError, OSError, OverflowError, ValueError):
        return float("-inf")


class StarredMessagesDialog(wx.Dialog):
    _SORT_OPTIONS = (
        "Más recientes primero",
        "Más antiguos primero",
        "Texto: A a Z",
        "Texto: Z a A",
    )

    def __init__(
        self,
        parent: wx.Window,
        chat_name: str,
        loader: MessagesLoader,
        on_open_message: MessageKeyAction | None = None,
        on_speak_message: MessageKeyAction | None = None,
        on_play_audio: MessageKeyAction | None = None,
        on_describe: MessageDescribeAction | None = None,
    ) -> None:
        super().__init__(
            parent,
            title=f"Mensajes destacados de {chat_name}",
            size=(820, 520),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._loader = loader
        self._on_open_message = on_open_message
        self._on_speak_message = on_speak_message
        self._on_play_audio = on_play_audio
        self._on_describe = on_describe
        self._active = True
        self._messages: list[Message] = []
        self._visible_messages: list[Message] = []
        self.selected_message: Message | None = None

        self.status = wx.StaticText(self, label="Cargando mensajes destacados locales...")
        self.messages = wx.ListCtrl(
            self,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
        )
        self.messages.SetName("Mensajes destacados")
        self.messages.SetToolTip(
            "Enter: ir al mensaje. Espacio: leer el texto completo o reproducir el audio. "
            "Flecha izquierda: leer el texto con NVDA."
        )
        self.messages.InsertColumn(0, "Mensaje", width=560)
        self.messages.InsertColumn(1, "Fecha", width=190)
        sort_label = wx.StaticText(self, label="Ordenar:")
        self.sort_choice = wx.Choice(self, choices=self._SORT_OPTIONS)
        self.sort_choice.SetName("Orden de mensajes destacados")
        self.sort_choice.SetSelection(0)
        self.go_button = wx.Button(self, label="Ir al mensaje")
        close_button = wx.Button(self, wx.ID_CLOSE, "Cerrar")

        sort_row = wx.BoxSizer(wx.HORIZONTAL)
        sort_row.Add(sort_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        sort_row.Add(self.sort_choice, 0)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self.go_button, 0, wx.RIGHT, 8)
        buttons.Add(close_button, 0)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.status, 0, wx.ALL | wx.EXPAND, 12)
        box.Add(sort_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        box.Add(self.messages, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)
        box.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizer(box)
        self.SetMinSize((620, 380))

        self.go_button.Bind(wx.EVT_BUTTON, self._on_go_to_message)
        self.messages.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_go_to_message)
        self.messages.Bind(wx.EVT_CONTEXT_MENU, self._show_menu)
        self.messages.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self._on_right_click)
        self.sort_choice.Bind(wx.EVT_CHOICE, self._on_sort_changed)
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
        self._render_messages()

        if error:
            self.status.SetLabel(error)
        elif messages:
            self.status.SetLabel(f"{len(messages)} mensajes destacados guardados localmente.")
        else:
            self.status.SetLabel("No hay mensajes destacados guardados para este chat.")
        self.go_button.Enable(bool(messages) and not error)

    def _render_messages(self) -> None:
        messages = self._sorted_messages()
        self._visible_messages = messages
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

    def _sorted_messages(self) -> list[Message]:
        selection = self.sort_choice.GetSelection()
        if selection == 1:
            return sorted(self._messages, key=_message_sort_timestamp)
        if selection == 2:
            return sorted(
                self._messages,
                key=lambda message: _message_description(message).casefold(),
            )
        if selection == 3:
            return sorted(
                self._messages,
                key=lambda message: _message_description(message).casefold(),
                reverse=True,
            )
        return sorted(self._messages, key=_message_sort_timestamp, reverse=True)

    def _on_sort_changed(self, _event: wx.CommandEvent) -> None:
        self._render_messages()

    def _on_go_to_message(self, _event: wx.Event) -> None:
        message = self._selected_message()
        if message is None:
            return
        self.selected_message = message
        self.EndModal(wx.ID_OK)

    def _on_right_click(self, event: wx.ListEvent) -> None:
        self.messages.Select(event.GetIndex())
        self._show_menu(event)

    def _show_menu(self, _event: wx.Event) -> None:
        message = self._selected_message()
        if message is None:
            return

        menu = wx.Menu()
        go_item = menu.Append(wx.ID_ANY, "Ir al mensaje")
        self.Bind(wx.EVT_MENU, self._on_go_to_message, go_item)
        if self._on_describe is not None and can_describe_with_rayoai(message):
            describe_item = menu.Append(wx.ID_ANY, "Describir con RayoAI")
            self.Bind(
                wx.EVT_MENU,
                lambda _menu_event: self._describe(message),
                describe_item,
            )
        self.PopupMenu(menu)
        menu.Destroy()

    def _selected_message(self) -> Message | None:
        index = self.messages.GetFirstSelected()
        if index == wx.NOT_FOUND or index >= len(self._visible_messages):
            return None
        return self._visible_messages[index]

    def _describe(self, message: Message) -> None:
        if self._on_describe is None:
            return
        described_message = self._on_describe(message)
        if described_message is None or described_message is message:
            return
        for messages in (self._messages, self._visible_messages):
            for index, current in enumerate(messages):
                if current is message:
                    messages[index] = described_message

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        key_code = event.GetKeyCode()
        if key_code == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return

        message = self._selected_message()
        if message is not None and key_code == wx.WXK_SPACE:
            is_audio = bool(message.audio_url or message.media_kind == "audio")
            if is_audio:
                action = self._on_play_audio
            elif not has_media(message):
                action = self._on_open_message
            else:
                action = None
            if action is not None and action(message):
                return

        if (
            message is not None
            and key_code == wx.WXK_LEFT
            and self._on_speak_message is not None
            and self._on_speak_message(message)
        ):
            return

        event.Skip()


class ChatMessageSearchDialog(wx.Dialog):
    _MONTHS = (
        "01 - enero",
        "02 - febrero",
        "03 - marzo",
        "04 - abril",
        "05 - mayo",
        "06 - junio",
        "07 - julio",
        "08 - agosto",
        "09 - septiembre",
        "10 - octubre",
        "11 - noviembre",
        "12 - diciembre",
    )

    def __init__(
        self,
        parent: wx.Window,
        chat_name: str,
        loader: MessageSearchLoader,
        dates_loader: MessageDatesLoader,
    ) -> None:
        super().__init__(
            parent,
            title=f"Buscar mensajes en {chat_name}",
            size=(820, 560),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._loader = loader
        self._dates_loader = dates_loader
        self._active = True
        self._search_timer: wx.CallLater | None = None
        self._search_request_id = 0
        self._messages: list[Message] = []
        self.selected_message: Message | None = None

        self.status = wx.StaticText(self, label="Cargando fechas disponibles...")
        search_label = wx.StaticText(self, label="Buscar:")
        self.search_ctrl = wx.TextCtrl(self)
        self.search_ctrl.SetName("Buscar mensajes en este chat")
        self.search_ctrl.SetToolTip(
            "Busca solamente en los mensajes guardados de este chat."
        )
        self.date_filter = wx.CheckBox(self, label="Filtrar por fecha")
        self.year_choice = wx.Choice(self)
        self.year_choice.SetName("Año de la fecha")
        self.month_choice = wx.Choice(self, choices=self._MONTHS)
        self.month_choice.SetName("Mes de la fecha")
        self.day_choice = wx.Choice(self)
        self.day_choice.SetName("Día de la fecha")
        self.messages = wx.ListCtrl(
            self,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
        )
        self.messages.SetName("Resultados de búsqueda en este chat")
        self.messages.InsertColumn(0, "Mensaje", width=560)
        self.messages.InsertColumn(1, "Fecha", width=190)
        self.go_button = wx.Button(self, label="Ir al mensaje")
        close_button = wx.Button(self, wx.ID_CLOSE, "Cerrar")

        search_row = wx.BoxSizer(wx.HORIZONTAL)
        search_row.Add(search_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        search_row.Add(self.search_ctrl, 1, wx.EXPAND)
        date_row = wx.BoxSizer(wx.HORIZONTAL)
        date_row.Add(self.date_filter, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
        date_row.Add(wx.StaticText(self, label="Año:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        date_row.Add(self.year_choice, 0, wx.RIGHT, 10)
        date_row.Add(wx.StaticText(self, label="Mes:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        date_row.Add(self.month_choice, 0, wx.RIGHT, 10)
        date_row.Add(wx.StaticText(self, label="Día:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        date_row.Add(self.day_choice, 0)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self.go_button, 0, wx.RIGHT, 8)
        buttons.Add(close_button, 0)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.status, 0, wx.ALL | wx.EXPAND, 12)
        box.Add(search_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        box.Add(date_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        box.Add(self.messages, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)
        box.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizer(box)
        self.SetMinSize((620, 420))

        self.go_button.Enable(False)
        self._set_date_controls_enabled(False)
        self.search_ctrl.Bind(wx.EVT_TEXT, self._on_search_text_changed)
        self.date_filter.Bind(wx.EVT_CHECKBOX, self._on_date_filter_changed)
        self.year_choice.Bind(wx.EVT_CHOICE, self._on_year_or_month_changed)
        self.month_choice.Bind(wx.EVT_CHOICE, self._on_year_or_month_changed)
        self.day_choice.Bind(wx.EVT_CHOICE, self._on_date_changed)
        self.go_button.Bind(wx.EVT_BUTTON, self._on_go_to_message)
        self.messages.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_go_to_message)
        self.Bind(wx.EVT_BUTTON, lambda _event: self.EndModal(wx.ID_CLOSE), close_button)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)
        apply_theme(self)
        wx.CallAfter(self._load_dates)
        wx.CallAfter(self.search_ctrl.SetFocus)

    def deactivate(self) -> None:
        self._active = False
        self._cancel_scheduled_search()

    def _load_dates(self) -> None:
        if not self._active:
            return

        def loaded(dates: list[date], error: str) -> None:
            if self._active:
                self._finish_dates_load(dates, error)

        self._dates_loader(loaded)

    def _finish_dates_load(self, dates: list[date], error: str) -> None:
        initial_date = dates[0] if dates else date.today()
        years = sorted({item.year for item in dates} | {initial_date.year}, reverse=True)
        self.year_choice.Set([str(year) for year in years])
        self.year_choice.SetSelection(0)
        self.month_choice.SetSelection(initial_date.month - 1)
        self._set_day_choices(initial_date.day)
        self._show_search_prompt(error)

    def _set_day_choices(self, preferred_day: int | None = None) -> None:
        selected_year = self._selected_choice_number(self.year_choice)
        selected_month = self.month_choice.GetSelection() + 1
        if selected_year is None or selected_month <= 0:
            return
        last_day = calendar.monthrange(selected_year, selected_month)[1]
        self.day_choice.Set([str(day) for day in range(1, last_day + 1)])
        day = min(preferred_day or 1, last_day)
        self.day_choice.SetSelection(day - 1)

    @staticmethod
    def _selected_choice_number(control: wx.Choice) -> int | None:
        selected = control.GetSelection()
        if selected == wx.NOT_FOUND:
            return None
        try:
            return int(control.GetString(selected).split(" ", 1)[0])
        except ValueError:
            return None

    def _set_date_controls_enabled(self, enabled: bool) -> None:
        for control in (self.year_choice, self.month_choice, self.day_choice):
            control.Enable(enabled)

    def _selected_date(self) -> date | None:
        if not self.date_filter.GetValue():
            return None
        year = self._selected_choice_number(self.year_choice)
        month = self.month_choice.GetSelection() + 1
        day = self._selected_choice_number(self.day_choice)
        if year is None or month <= 0 or day is None:
            return None
        try:
            return date(year, month, day)
        except ValueError:
            return None

    def _on_search_text_changed(self, event: wx.CommandEvent) -> None:
        self._schedule_search()
        event.Skip()

    def _on_date_filter_changed(self, _event: wx.CommandEvent) -> None:
        self._set_date_controls_enabled(self.date_filter.GetValue())
        self._schedule_search()

    def _on_year_or_month_changed(self, _event: wx.CommandEvent) -> None:
        current_day = self._selected_choice_number(self.day_choice)
        self._set_day_choices(current_day)
        self._schedule_search()

    def _on_date_changed(self, _event: wx.CommandEvent) -> None:
        self._schedule_search()

    def _schedule_search(self) -> None:
        self._cancel_scheduled_search()
        self._search_timer = wx.CallLater(250, self._search)

    def _cancel_scheduled_search(self) -> None:
        if self._search_timer is not None and self._search_timer.IsRunning():
            self._search_timer.Stop()

    def _search(self) -> None:
        if not self._active:
            return
        query = self.search_ctrl.GetValue().strip()
        sent_on = self._selected_date()
        self._search_request_id += 1
        request_id = self._search_request_id
        if not query and sent_on is None:
            self._show_search_prompt()
            return
        self.go_button.Enable(False)
        self.status.SetLabel("Buscando mensajes locales...")

        def loaded(messages: list[Message], error: str) -> None:
            if (
                self._active
                and request_id == self._search_request_id
                and self.search_ctrl.GetValue().strip() == query
                and self._selected_date() == sent_on
            ):
                self._finish_search(messages, error)

        self._loader(query, sent_on, loaded)

    def _finish_search(self, messages: list[Message], error: str) -> None:
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
            self.status.SetLabel(f"{len(messages)} mensajes encontrados en este chat.")
        else:
            self.status.SetLabel("No se encontraron mensajes en este chat.")
        self.go_button.Enable(bool(messages) and not error)

    def _show_search_prompt(self, error: str = "") -> None:
        self._messages = []
        self.messages.DeleteAllItems()
        self.go_button.Enable(False)
        self.status.SetLabel(
            error or "Escribe texto o activa el filtro por fecha para buscar en este chat."
        )

    def _on_go_to_message(self, _event: wx.Event) -> None:
        index = self.messages.GetFirstSelected()
        if index == wx.NOT_FOUND or index >= len(self._messages):
            return
        self.selected_message = self._messages[index]
        self.EndModal(wx.ID_OK)

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if self.messages.HasFocus() and self.messages.GetFirstSelected() != wx.NOT_FOUND:
                self._on_go_to_message(event)
                return
            self._cancel_scheduled_search()
            self._search()
            return
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
        on_describe: MessageDescribeAction | None = None,
        on_speak_message: MessageKeyAction | None = None,
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
        self._on_describe = on_describe
        self._on_speak_message = on_speak_message
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
        messages.SetToolTip("Flecha izquierda: leer el texto alternativo completo con NVDA.")
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
        describe_item: wx.MenuItem | None = None
        if self._on_describe is not None and can_describe_with_rayoai(message):
            describe_item = menu.Append(wx.ID_ANY, "Describir con RayoAI")
        copy_item.Enable(local_media_path(message) is not None or bool(message_links(message)))
        delete_item.Enable(local_media_path(message) is not None)
        self.Bind(wx.EVT_MENU, lambda _event: self._open(control), open_item)
        self.Bind(wx.EVT_MENU, lambda _event: self._copy(control), copy_item)
        self.Bind(wx.EVT_MENU, lambda _event: self._delete(control), delete_item)
        if describe_item is not None:
            self.Bind(
                wx.EVT_MENU,
                lambda _event: self._describe(control),
                describe_item,
            )
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

    def _describe(self, control: wx.ListCtrl) -> None:
        message = self._selected_message(control)
        if message is not None and self._on_describe is not None:
            described_message = self._on_describe(message)
            if described_message is None or described_message is message:
                return
            for index, current in enumerate(self._messages):
                if current is message:
                    self._messages[index] = described_message
            messages = self._messages_by_list.get(id(control), [])
            for index, current in enumerate(messages):
                if current is message:
                    messages[index] = described_message

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return

        if event.GetKeyCode() == wx.WXK_LEFT and self._on_speak_message is not None:
            control = self.notebook.GetCurrentPage().GetChildren()[0]
            message = self._selected_message(control)
            if message is not None and self._on_speak_message(message):
                return
        event.Skip()
