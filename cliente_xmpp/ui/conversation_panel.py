from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import wx

from cliente_xmpp.accessibility.speaker import NvdaSpeaker
from cliente_xmpp.audio.duration import media_duration_seconds
from cliente_xmpp.audio.player import MpvAudioPlayer, MpvPlaybackError
from cliente_xmpp.media.downloads import (
    audio_description,
    format_duration,
    local_media_path,
    media_description,
)
from cliente_xmpp.models.chat import Chat, Message


class ConversationPanel(wx.Panel):
    def __init__(
        self,
        parent: wx.Window,
        resolve_display_name: Callable[[str], str],
        initial_audio_speed: float = 1.0,
        on_audio_speed_changed: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.resolve_display_name = resolve_display_name
        self.on_audio_speed_changed = on_audio_speed_changed
        self.current_chat: Chat | None = None
        self._messages: list[Message] = []
        self._message_rows: list[Message | None] = []
        self._unread_marker_count = 0
        self._unread_marker_index: int | None = None
        self._focus_target_index: int | None = None
        self._reply_quote_prefix = ""
        self._audio_durations_by_url: dict[str, float] = {}
        self._thumbnail_indexes_by_path: dict[str, int] = {}
        self._thumbnail_images = wx.ImageList(48, 48)
        self._audio_player = MpvAudioPlayer(speed=initial_audio_speed)
        self._video_player = MpvAudioPlayer(video=True)
        self._speaker = NvdaSpeaker()
        self._audio_autoplay_timer = wx.Timer(self)
        self._current_audio_row_index: int | None = None

        self.title = wx.StaticText(self, label="Selecciona un chat")
        self.load_older_button = wx.Button(self, label="Cargar mensajes anteriores...")
        self.back_button = wx.Button(self, label="Volver")
        self.messages = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_NONE)
        self.compose: wx.TextCtrl
        self.attach_button: wx.Button
        self.send_button: wx.Button
        self.pause_recording_button: wx.Button
        self.cancel_recording_button: wx.Button

        self._layout()
        self.Bind(wx.EVT_TIMER, self._on_audio_autoplay_timer, self._audio_autoplay_timer)

    def set_chat(self, chat: Chat) -> None:
        self.current_chat = chat
        self.title.SetLabel(chat.name)
        self.messages.DeleteAllItems()
        self._messages = []
        self._message_rows = []
        self._unread_marker_count = 0
        self._unread_marker_index = None
        self._focus_target_index = None
        self._reply_quote_prefix = ""
        self.send_button.Enable(True)
        self.attach_button.Enable(True)
        self.set_recording_state(False)
        self.update_send_button_state()
        self.load_older_button.Enable(True)

    def set_messages(self, messages: list[Message], unread_count: int = 0) -> None:
        self.messages.DeleteAllItems()
        self._messages = list(messages)
        self._message_rows = []
        self._unread_marker_count = max(0, unread_count)
        self._unread_marker_index = None
        self._focus_target_index = None

        marker_message_index = self._unread_marker_message_index(
            len(self._messages),
            self._unread_marker_count,
        )
        for message_index, message in enumerate(self._messages):
            if marker_message_index == message_index:
                self._insert_unread_marker()
            self._append_message_row(message)

        if marker_message_index == len(self._messages) and self._unread_marker_count > 0:
            self._insert_unread_marker()

        if self._unread_marker_index is not None:
            self._focus_target_index = self._unread_marker_index
        elif self._message_rows:
            self._focus_target_index = len(self._message_rows) - 1

        self._resize_message_column_to_content()
        if self.messages.HasFocus():
            wx.CallAfter(self.focus_default_message_item)

    def append_message(self, message: Message) -> None:
        self._messages.append(message)
        index = self._append_message_row(message)
        if self._unread_marker_index is None:
            self._focus_target_index = index
        self._resize_message_column_to_content()
        self.messages.EnsureVisible(index)

    def focus_composer(self) -> None:
        self.compose.SetFocus()

    def focus_default_message_item(self) -> None:
        if self._focus_target_index is None:
            return

        item_count = self.messages.GetItemCount()
        if item_count <= 0:
            return

        index = min(self._focus_target_index, item_count - 1)
        self._clear_message_selection()
        self.messages.SetItemState(
            index,
            wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
            wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
        )
        self.messages.EnsureVisible(index)

    def clear_unread_marker(self) -> None:
        if self._unread_marker_index is None:
            return

        self.set_messages(self._messages)

    def unread_marker_count(self) -> int:
        return self._unread_marker_count

    def consume_composed_message(self) -> str:
        body = self.compose.GetValue().strip()
        if body:
            self.compose.Clear()
            self.update_send_button_state()
        return body

    def has_composed_text(self) -> bool:
        return bool(self.compose.GetValue().strip())

    def update_send_button_state(self, recording: bool = False, paused: bool = False) -> None:
        if recording:
            self.send_button.SetLabel("&Detener y enviar")
            self.pause_recording_button.SetLabel("Reanudar" if paused else "Pausar")
            return

        if self.has_composed_text():
            self.send_button.SetLabel("&Enviar")
        else:
            self.send_button.SetLabel("&Grabar audio")

    def set_recording_state(self, recording: bool, paused: bool = False) -> None:
        self.compose.Enable(not recording)
        self.attach_button.Enable(not recording)
        self.pause_recording_button.Show(recording)
        self.cancel_recording_button.Show(recording)
        self.update_send_button_state(recording, paused)
        self.Layout()

    def insert_reply_quote(self, message: Message) -> None:
        sender = "Tú" if message.outgoing else self.resolve_display_name(message.sender_jid)
        quote = self._format_reply_quote(sender, message.body)
        self._reply_quote_prefix = quote
        current_body = self.compose.GetValue()
        if current_body.startswith(quote):
            self.compose.SetFocus()
            return

        self.compose.SetValue(f"{quote}{current_body}")
        self.compose.SetInsertionPointEnd()
        self.compose.SetFocus()

    def reply_fallback_end(self, body: str) -> int:
        if not self._reply_quote_prefix:
            return 0

        if not body.startswith(self._reply_quote_prefix):
            return 0

        return len(self._reply_quote_prefix)

    def clear_reply_quote(self) -> None:
        self._reply_quote_prefix = ""

    def open_selected_message_reader(self) -> bool:
        message = self.selected_message()
        if message is None:
            return False

        dialog = MessageReaderDialog(
            self,
            title="Mensaje",
            body=self._format_message_for_reader(message),
        )
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()
        return True

    def selected_message(self) -> Message | None:
        index = self.messages.GetFirstSelected()
        if index == wx.NOT_FOUND or index >= len(self._message_rows):
            return None

        return self._message_rows[index]

    def refresh_message(self, message: Message) -> None:
        for index, current in enumerate(self._message_rows):
            if current is not message:
                continue

            self.messages.SetItem(index, 0, self._format_message_row(message))
            image_index = self._thumbnail_index_for_message(message)
            if image_index >= 0:
                item = self.messages.GetItem(index)
                item.SetImage(image_index)
                self.messages.SetItem(item)
            self._resize_message_column_to_content()
            return

    def play_selected_audio(self) -> bool:
        index = self.messages.GetFirstSelected()
        if index == wx.NOT_FOUND or index >= len(self._message_rows):
            return False

        message = self._message_rows[index]
        if message is None:
            return False

        audio_url = message.audio_url
        if not audio_url:
            return False

        try:
            status = self._audio_player.play(audio_url)
        except MpvPlaybackError as exc:
            wx.MessageBox(str(exc), "Audio")
        else:
            self._speaker.speak("Pausado" if status == "paused" else "Reproduciendo")
            self._schedule_audio_duration_update(index, audio_url)
            if status == "playing":
                self._current_audio_row_index = index
                self._audio_autoplay_timer.Start(500)
            else:
                self._audio_autoplay_timer.Stop()

        return True

    def play_selected_video(self) -> bool:
        index = self.messages.GetFirstSelected()
        if index == wx.NOT_FOUND or index >= len(self._message_rows):
            return False

        message = self._message_rows[index]
        if message is None or message.media_kind != "video":
            return False

        source = str(local_media_path(message) or message.media_url)
        if not source:
            return False

        try:
            status = self._video_player.play(source)
        except MpvPlaybackError as exc:
            wx.MessageBox(str(exc), "Video")
            return True

        self._speaker.speak("Pausado" if status == "paused" else "Reproduciendo")
        return True

    def cycle_selected_audio_speed(self) -> float | None:
        index = self.messages.GetFirstSelected()
        if index == wx.NOT_FOUND or index >= len(self._message_rows):
            return None

        message = self._message_rows[index]
        if message is None or not message.audio_url:
            return None

        try:
            speed = self._audio_player.cycle_speed(message.audio_url)
        except MpvPlaybackError as exc:
            wx.MessageBox(str(exc), "Audio")
            return None

        self._speaker.speak(f"Velocidad {speed:g}x")
        if self.on_audio_speed_changed is not None:
            self.on_audio_speed_changed(speed)
        self._schedule_audio_duration_update(index, message.audio_url)
        return speed

    def _on_audio_autoplay_timer(self, _event: wx.TimerEvent) -> None:
        index = self._current_audio_row_index
        if index is None or index >= len(self._message_rows):
            self._audio_autoplay_timer.Stop()
            return

        message = self._message_rows[index]
        if message is None or not message.audio_url:
            self._audio_autoplay_timer.Stop()
            return

        if not self._audio_player.is_finished(message.audio_url):
            return

        next_index = index + 1
        if next_index >= len(self._message_rows):
            self._audio_autoplay_timer.Stop()
            return

        next_message = self._message_rows[next_index]
        if next_message is None or not next_message.audio_url:
            self._audio_autoplay_timer.Stop()
            return

        self._clear_message_selection()
        self.messages.SetItemState(
            next_index,
            wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
            wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
        )
        self.messages.EnsureVisible(next_index)
        try:
            self._audio_player.play(next_message.audio_url)
        except MpvPlaybackError as exc:
            self._audio_autoplay_timer.Stop()
            wx.MessageBox(str(exc), "Audio")
            return

        self._current_audio_row_index = next_index
        self._speaker.speak("Reproduciendo")
        self._schedule_audio_duration_update(next_index, next_message.audio_url)

    def close_audio(self) -> None:
        self._audio_autoplay_timer.Stop()
        self._audio_player.close()
        self._video_player.close()

    def current_audio_speed(self) -> float:
        return self._audio_player.speed

    def _layout(self) -> None:
        header = wx.BoxSizer(wx.HORIZONTAL)
        header.Add(self.title, 1, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 12)
        header.Add(self.load_older_button, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 12)
        header.Add(self.back_button, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 12)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(header, 0, wx.EXPAND)
        box.Add(self.messages, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)
        self.messages.InsertColumn(0, "Mensajes", width=820)
        self.messages.AssignImageList(self._thumbnail_images, wx.IMAGE_LIST_SMALL)

        box.Add(wx.StaticText(self, label="Mensaje:"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)

        composer = wx.BoxSizer(wx.HORIZONTAL)
        self.compose = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        self.compose.SetToolTip("Escribe el mensaje para el chat seleccionado.")
        composer.Add(self.compose, 1, wx.EXPAND | wx.RIGHT, 8)

        self.send_button = wx.Button(self, label="Enviar")
        self.send_button.Enable(False)
        self.attach_button = wx.Button(self, label="&Adjuntar")
        self.attach_button.Enable(False)
        self.pause_recording_button = wx.Button(self, label="Pausar")
        self.cancel_recording_button = wx.Button(self, label="Cancelar")
        self.pause_recording_button.Hide()
        self.cancel_recording_button.Hide()
        composer.Add(self.attach_button, 0, wx.EXPAND | wx.RIGHT, 8)
        composer.Add(self.pause_recording_button, 0, wx.EXPAND | wx.RIGHT, 8)
        composer.Add(self.cancel_recording_button, 0, wx.EXPAND | wx.RIGHT, 8)
        composer.Add(self.send_button, 0, wx.EXPAND)

        box.Add(composer, 0, wx.ALL | wx.EXPAND, 12)
        self.SetSizer(box)
        self.messages.Bind(wx.EVT_SET_FOCUS, self._on_messages_focus)
        self.messages.Bind(wx.EVT_LIST_ITEM_FOCUSED, self._on_message_item_focused)

    def _on_messages_focus(self, event: wx.FocusEvent) -> None:
        if self.messages.GetFirstSelected() == wx.NOT_FOUND:
            wx.CallAfter(self.focus_default_message_item)
        event.Skip()

    def _on_message_item_focused(self, event: wx.ListEvent) -> None:
        index = event.GetIndex()
        if index != wx.NOT_FOUND and index < len(self._message_rows):
            text = self._format_row_for_tooltip(index)
            if text:
                self.messages.SetToolTip(text)
        event.Skip()

    def _append_message_row(self, message: Message) -> int:
        index = self.messages.GetItemCount()
        self._message_rows.append(message)
        image_index = self._thumbnail_index_for_message(message)
        if image_index >= 0:
            self.messages.InsertItem(index, self._format_message_row(message), image_index)
        else:
            self.messages.InsertItem(index, self._format_message_row(message))
        return index

    def _insert_unread_marker(self) -> None:
        self._unread_marker_index = self.messages.GetItemCount()
        self._message_rows.append(None)
        self.messages.InsertItem(self._unread_marker_index, "No leídos")

    def _resize_message_column_to_content(self) -> None:
        if self.messages.GetItemCount() <= 0:
            self.messages.SetColumnWidth(0, 820)
            return

        self.messages.SetColumnWidth(0, wx.LIST_AUTOSIZE)
        self.messages.SetColumnWidth(0, max(self.messages.GetColumnWidth(0), 820))

    def _clear_message_selection(self) -> None:
        selected = self.messages.GetFirstSelected()
        while selected != wx.NOT_FOUND:
            self.messages.Select(selected, False)
            selected = self.messages.GetFirstSelected()

    @staticmethod
    def _unread_marker_message_index(message_count: int, unread_count: int) -> int | None:
        if unread_count <= 0:
            return None

        return max(0, message_count - unread_count)

    @staticmethod
    def _format_reply_quote(sender: str, body: str) -> str:
        quoted_lines = body.splitlines() or [body]
        quoted_body = "\n".join(f"> {line}" if line else ">" for line in quoted_lines)
        return f"> {sender}:\n{quoted_body}\n"

    def _format_message_row(self, message: Message) -> str:
        timestamp = self._format_message_time(message)
        body = self._format_message_body(message)
        starred = "Destacado. " if message.starred else ""
        reactions = f" Reacciones: {' '.join(message.reactions)}." if message.reactions else ""
        reply = self._format_reply_summary(message)
        if message.outgoing:
            if reply:
                return f"{starred}Tú, {body}, {reply}, {timestamp} Entregado.{reactions}"
            return f"{starred}Tú {body} {timestamp} Entregado.{reactions}"

        sender = self.resolve_display_name(message.sender_jid)
        if reply:
            return f"{starred}{sender}, {body}, {reply}, {timestamp}.{reactions}"

        return f"{starred}{sender} {body}, {timestamp}.{reactions}"

    def _format_message_for_reader(self, message: Message) -> str:
        sender = "Tú" if message.outgoing else self.resolve_display_name(message.sender_jid)
        timestamp = self._format_message_time(message)
        body = self._format_message_body(message)
        metadata = f"{sender} {timestamp}"
        if message.starred:
            metadata = f"Destacado. {metadata}"
        if message.reactions:
            metadata = f"{metadata}\nReacciones: {' '.join(message.reactions)}"

        reply = self._format_reply_summary(message)
        if reply:
            return f"{metadata}\n{reply}\n\n{body}"

        return f"{metadata}\n\n{body}"

    def _format_reply_summary(self, message: Message) -> str:
        if not message.reply_quote:
            return ""

        return f"respondiendo a: {self._truncate_reply_quote(message.reply_quote)}"

    @staticmethod
    def _truncate_reply_quote(quote: str, max_length: int = 300) -> str:
        quote = " ".join(quote.split())
        if len(quote) <= max_length:
            return quote

        return f"{quote[: max_length - 3]}..."

    def _format_row_for_tooltip(self, index: int) -> str:
        row = self._message_rows[index]
        if row is None:
            return "No leídos"

        return self._format_message_row(row)

    def _format_message_body(self, message: Message) -> str:
        if message.media_url and message.media_kind != "audio":
            return media_description(message)

        if not message.audio_url:
            return message.body

        path = local_media_path(message)
        if message.media_duration_seconds <= 0 and path is not None:
            message.media_duration_seconds = media_duration_seconds(path)

        if message.media_duration_seconds > 0:
            return audio_description(message)

        duration = self._audio_durations_by_url.get(message.audio_url)
        if duration is None:
            return "Mensaje de voz"

        return f"Mensaje de voz, {format_duration(duration)}"

    def _thumbnail_index_for_message(self, message: Message) -> int:
        if message.media_kind != "image":
            return -1

        path = local_media_path(message)
        if path is None:
            return -1

        return self._thumbnail_index_for_path(path)

    def _thumbnail_index_for_path(self, path: Path) -> int:
        key = str(path)
        if key in self._thumbnail_indexes_by_path:
            return self._thumbnail_indexes_by_path[key]

        try:
            image = wx.Image(key)
            if not image.IsOk():
                return -1
            image = image.Scale(48, 48, wx.IMAGE_QUALITY_HIGH)
            index = self._thumbnail_images.Add(wx.Bitmap(image))
        except Exception:
            return -1

        self._thumbnail_indexes_by_path[key] = index
        return index

    def _format_message_time(self, message: Message) -> str:
        hour = message.sent_at.hour
        minute = message.sent_at.minute
        suffix = "a. m." if hour < 12 else "p. m."
        hour_12 = hour % 12 or 12
        return f"{hour_12}:{minute:02d} {suffix}"

    def _schedule_audio_duration_update(
        self,
        index: int,
        audio_url: str,
        attempts_left: int = 12,
    ) -> None:
        if audio_url in self._audio_durations_by_url:
            return

        wx.CallLater(250, self._update_audio_duration, index, audio_url, attempts_left)

    def _update_audio_duration(self, index: int, audio_url: str, attempts_left: int) -> None:
        if index >= len(self._message_rows):
            return

        message = self._message_rows[index]
        if message is None:
            return

        duration = self._audio_player.current_duration_seconds(audio_url)
        if duration is None:
            if attempts_left > 0:
                self._schedule_audio_duration_update(index, audio_url, attempts_left - 1)
            return

        self._audio_durations_by_url[audio_url] = duration
        message.media_duration_seconds = duration
        self.messages.SetItem(index, 0, self._format_message_row(message))
        self._resize_message_column_to_content()

    @staticmethod
    def _format_duration(duration_seconds: float) -> str:
        total_seconds = max(0, round(duration_seconds))
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes}:{seconds:02d}"


class MessageReaderDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, title: str, body: str) -> None:
        super().__init__(parent, title=title, size=(720, 520))

        text = wx.TextCtrl(
            self,
            value=body,
            style=wx.TE_MULTILINE | wx.TE_READONLY,
        )
        text.SetInsertionPoint(0)

        close_button = wx.Button(self, wx.ID_OK, "Cerrar")

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(text, 1, wx.ALL | wx.EXPAND, 12)
        box.Add(close_button, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.ALIGN_RIGHT, 12)
        self.SetSizer(box)
        self.Bind(wx.EVT_BUTTON, lambda _event: self.EndModal(wx.ID_OK), close_button)
        wx.CallAfter(text.SetFocus)
