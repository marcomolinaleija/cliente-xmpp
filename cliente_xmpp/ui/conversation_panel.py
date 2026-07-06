from __future__ import annotations

from collections.abc import Callable

import wx

from cliente_xmpp.accessibility.speaker import NvdaSpeaker
from cliente_xmpp.audio.player import MpvAudioPlayer, MpvPlaybackError
from cliente_xmpp.models.chat import Chat, Message


class ConversationPanel(wx.Panel):
    def __init__(self, parent: wx.Window, resolve_display_name: Callable[[str], str]) -> None:
        super().__init__(parent)
        self.resolve_display_name = resolve_display_name
        self.current_chat: Chat | None = None
        self._messages: list[Message] = []
        self._audio_durations_by_url: dict[str, float] = {}
        self._audio_player = MpvAudioPlayer()
        self._speaker = NvdaSpeaker()

        self.title = wx.StaticText(self, label="Selecciona un chat")
        self.load_older_button = wx.Button(self, label="Cargar mensajes anteriores...")
        self.back_button = wx.Button(self, label="Volver")
        self.messages = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_NONE)
        self.compose: wx.TextCtrl
        self.send_button: wx.Button

        self._layout()

    def set_chat(self, chat: Chat) -> None:
        self.current_chat = chat
        self.title.SetLabel(chat.name)
        self.messages.DeleteAllItems()
        self._messages = []
        self.send_button.Enable(True)
        self.load_older_button.Enable(True)

    def append_message(self, message: Message) -> None:
        index = self.messages.GetItemCount()
        self._messages.append(message)
        self.messages.InsertItem(index, self._format_message_row(message))
        self.messages.EnsureVisible(index)

    def focus_composer(self) -> None:
        self.compose.SetFocus()

    def consume_composed_message(self) -> str:
        body = self.compose.GetValue().strip()
        if body:
            self.compose.Clear()
        return body

    def play_selected_audio(self) -> bool:
        index = self.messages.GetFirstSelected()
        if index == wx.NOT_FOUND or index >= len(self._messages):
            return False

        audio_url = self._messages[index].audio_url
        if not audio_url:
            return False

        try:
            status = self._audio_player.play(audio_url)
        except MpvPlaybackError as exc:
            wx.MessageBox(str(exc), "Audio")
        else:
            self._speaker.speak("Pausado" if status == "paused" else "Reproduciendo")
            self._schedule_audio_duration_update(index, audio_url)

        return True

    def close_audio(self) -> None:
        self._audio_player.close()

    def _layout(self) -> None:
        header = wx.BoxSizer(wx.HORIZONTAL)
        header.Add(self.title, 1, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 12)
        header.Add(self.load_older_button, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 12)
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
        body = self._format_message_body(message)
        if message.outgoing:
            return f"Tú {body} {timestamp} Entregado."

        sender = self.resolve_display_name(message.sender_jid)
        return f"{sender} {body}, {timestamp}"

    def _format_message_body(self, message: Message) -> str:
        if not message.audio_url:
            return message.body

        duration = self._audio_durations_by_url.get(message.audio_url)
        if duration is None:
            return "Mensaje de voz"

        return f"Mensaje de voz ({self._format_duration(duration)})"

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
        if index >= len(self._messages):
            return

        duration = self._audio_player.current_duration_seconds(audio_url)
        if duration is None:
            if attempts_left > 0:
                self._schedule_audio_duration_update(index, audio_url, attempts_left - 1)
            return

        self._audio_durations_by_url[audio_url] = duration
        self.messages.SetItem(index, 0, self._format_message_row(self._messages[index]))

    @staticmethod
    def _format_duration(duration_seconds: float) -> str:
        total_seconds = max(0, round(duration_seconds))
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes}:{seconds:02d}"
