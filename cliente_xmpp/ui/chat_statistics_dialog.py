from __future__ import annotations

from collections.abc import Callable

import wx

from cliente_xmpp.models.statistics import (
    LocalChatStatistics,
    ParticipantChatStatistics,
)
from cliente_xmpp.ui.statistics_dialog import PERIODS, StatisticsDialog
from cliente_xmpp.ui.theme import apply_theme

ChatStatisticsLoadedCallback = Callable[[LocalChatStatistics | None, str], None]
ChatStatisticsLoader = Callable[[int | None, ChatStatisticsLoadedCallback], None]


class ChatStatisticsDialog(wx.Dialog):
    def __init__(
        self,
        parent: wx.Window,
        chat_name: str,
        loader: ChatStatisticsLoader,
    ) -> None:
        super().__init__(
            parent,
            title=f"Estadísticas locales de {chat_name}",
            size=(900, 680),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._loader = loader
        self._active = True
        self._request_id = 0
        self._statistics: LocalChatStatistics | None = None
        self._visible_participants: tuple[ParticipantChatStatistics, ...] = ()

        period_label = wx.StaticText(self, label="Período:")
        self.period_choice = wx.Choice(self, choices=[label for label, _days in PERIODS])
        self.period_choice.SetSelection(1)
        self.period_choice.SetName("Período de las estadísticas del chat")
        self.refresh_button = wx.Button(self, label="&Actualizar")
        self.status = wx.StaticText(
            self,
            label="Sólo se analizan mensajes guardados localmente para este chat.",
        )

        self.notebook = wx.Notebook(self)
        self.notebook.SetName("Secciones de estadísticas locales del chat")
        self.summary = self._create_summary_page()
        self.participants = self._create_participants_page()
        self.phrases = self._create_phrases_page()
        close_button = wx.Button(self, wx.ID_CLOSE, "&Cerrar")

        filters = wx.BoxSizer(wx.HORIZONTAL)
        filters.Add(period_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        filters.Add(self.period_choice, 0, wx.RIGHT, 8)
        filters.Add(self.refresh_button, 0)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(filters, 0, wx.ALL | wx.EXPAND, 12)
        box.Add(self.status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        box.Add(self.notebook, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)
        box.Add(close_button, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizer(box)
        self.SetMinSize((760, 520))

        self.Bind(wx.EVT_BUTTON, self._on_refresh, self.refresh_button)
        self.Bind(wx.EVT_CHOICE, self._on_period_changed, self.period_choice)
        self.participants.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_participant_selected)
        self.Bind(wx.EVT_BUTTON, self._on_close_button, close_button)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        apply_theme(self)
        wx.CallAfter(self.refresh)

    def deactivate(self) -> None:
        self._active = False
        self._request_id += 1

    def refresh(self) -> None:
        if not self._active:
            return
        self._request_id += 1
        request_id = self._request_id
        period_days = PERIODS[self.period_choice.GetSelection()][1]
        self.refresh_button.Enable(False)
        self.period_choice.Enable(False)
        self.status.SetLabel("Calculando estadísticas locales del chat...")
        self.summary.ChangeValue("Calculando estadísticas. Espera por favor...")

        def loaded(statistics: LocalChatStatistics | None, error: str) -> None:
            self._finish_load(request_id, statistics, error)

        self._loader(period_days, loaded)

    def _create_summary_page(self) -> wx.TextCtrl:
        page = wx.Panel(self.notebook)
        summary = wx.TextCtrl(
            page,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_NONE,
        )
        summary.SetName("Resumen local del chat")
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(summary, 1, wx.ALL | wx.EXPAND, 12)
        page.SetSizer(box)
        self.notebook.AddPage(page, "Resumen")
        return summary

    def _create_participants_page(self) -> wx.ListCtrl:
        page = wx.Panel(self.notebook)
        note = wx.StaticText(
            page,
            label=(
                "La lista muestra cuánto participa cada persona y la tendencia aproximada "
                "del lenguaje de sus mensajes. En un chat individual aparecen Tú y el contacto."
            ),
        )
        participants = wx.ListCtrl(
            page,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
        )
        participants.SetName("Actividad por persona")
        for index, (label, width) in enumerate(
            (
                ("Persona", 230),
                ("Mensajes", 90),
                ("Participación", 110),
                ("Hora pico", 110),
                ("Intervalo típico", 140),
                ("Lenguaje detectado", 180),
            )
        ):
            participants.InsertColumn(index, label, width=width)

        self.participant_detail = wx.TextCtrl(
            page,
            value="Selecciona una persona para ver el detalle.",
            style=wx.TE_MULTILINE | wx.TE_READONLY,
        )
        self.participant_detail.SetName("Detalle de la persona seleccionada")
        detail_box = wx.StaticBoxSizer(wx.VERTICAL, page, "Detalle por persona")
        detail_box.Add(self.participant_detail, 1, wx.ALL | wx.EXPAND, 8)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(note, 0, wx.ALL | wx.EXPAND, 10)
        box.Add(participants, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        box.Add(detail_box, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        page.SetSizer(box)
        self.notebook.AddPage(page, "Personas")
        return participants

    def _create_phrases_page(self) -> wx.ListCtrl:
        page = wx.Panel(self.notebook)
        note = wx.StaticText(
            page,
            label=(
                "Frases de dos a cinco palabras que aparecen en al menos dos mensajes. "
                "Se calculan localmente y no se envía texto a ningún servicio."
            ),
        )
        phrases = wx.ListCtrl(
            page,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
        )
        phrases.SetName("Frases recurrentes del chat")
        phrases.InsertColumn(0, "Frase", width=620)
        phrases.InsertColumn(1, "Mensajes en que aparece", width=190)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(note, 0, wx.ALL | wx.EXPAND, 10)
        box.Add(phrases, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        page.SetSizer(box)
        self.notebook.AddPage(page, "Frases recurrentes")
        return phrases

    def _finish_load(
        self,
        request_id: int,
        statistics: LocalChatStatistics | None,
        error: str,
    ) -> None:
        if not self._active or request_id != self._request_id:
            return
        self.refresh_button.Enable(True)
        self.period_choice.Enable(True)
        if error or statistics is None:
            detail = error or "No se pudieron calcular las estadísticas del chat."
            self.status.SetLabel(detail)
            self.summary.ChangeValue(detail)
            self.summary.SetInsertionPoint(0)
            return
        self._render(statistics)

    def _render(self, statistics: LocalChatStatistics) -> None:
        self._statistics = statistics
        self.summary.ChangeValue(self._format_summary(statistics))
        self.summary.SetInsertionPoint(0)

        self.participants.Freeze()
        self.phrases.Freeze()
        try:
            self.participants.DeleteAllItems()
            self._visible_participants = statistics.participants
            total = statistics.overview.total if statistics.overview is not None else 0
            for participant in self._visible_participants:
                index = self.participants.InsertItem(
                    self.participants.GetItemCount(), participant.name
                )
                share = (participant.messages / total) * 100 if total else 0.0
                self.participants.SetItem(index, 1, str(participant.messages))
                self.participants.SetItem(index, 2, f"{share:.1f} %")
                self.participants.SetItem(
                    index,
                    3,
                    self._format_hour(participant.busiest_hour),
                )
                self.participants.SetItem(
                    index,
                    4,
                    StatisticsDialog._format_duration(participant.median_interval_seconds),
                )
                self.participants.SetItem(
                    index,
                    5,
                    StatisticsDialog._emotional_interpretation(
                        participant.positive_weight,
                        participant.negative_weight,
                    ),
                )
            if self._visible_participants:
                self.participants.Select(0)
                self.participants.Focus(0)
                self._show_participant_detail(self._visible_participants[0])
            else:
                self.participant_detail.ChangeValue("No hay personas para este período.")

            self.phrases.DeleteAllItems()
            for phrase in statistics.recurrent_phrases:
                index = self.phrases.InsertItem(self.phrases.GetItemCount(), phrase.phrase)
                self.phrases.SetItem(index, 1, str(phrase.occurrences))
        finally:
            self.participants.Thaw()
            self.phrases.Thaw()

        total = statistics.overview.total if statistics.overview is not None else 0
        self.status.SetLabel(
            f"{total} mensajes locales y {len(statistics.participants)} personas analizadas."
        )

    @classmethod
    def _format_summary(cls, statistics: LocalChatStatistics) -> str:
        chat = statistics.overview
        if chat is None:
            return (
                "No hay mensajes guardados para este chat durante el período seleccionado.\n\n"
                "Las estadísticas son locales: el historial remoto que aún no se haya "
                "descargado no se incluye."
            )

        daily_average = chat.total / max(1, chat.active_days)
        from_date_label = (
            statistics.from_date.strftime("%d/%m/%Y")
            if statistics.from_date
            else "sin datos"
        )
        emotional_reading = StatisticsDialog._emotional_interpretation(
            chat.positive_weight,
            chat.negative_weight,
        )
        lines = [
            f"Chat: {statistics.name}",
            f"Período: {from_date_label} "
            f"a {statistics.to_date.strftime('%d/%m/%Y')}",
            "",
            "Actividad",
            f"Mensajes: {chat.total}; {chat.sent} tuyos y {chat.received} recibidos",
            f"Días con actividad: {chat.active_days}",
            f"Frecuencia: {daily_average:.1f} mensajes por día activo",
            f"Primera actividad: {StatisticsDialog._format_datetime(chat.first_message_at)}",
            f"Última actividad: {StatisticsDialog._format_datetime(chat.last_message_at)}",
            f"Horas pico: {cls._format_peak_hours(statistics.hourly_activity)}",
            "",
            "Ritmo e intervalos",
            (
                "Intervalo típico entre mensajes: "
                f"{StatisticsDialog._format_duration(statistics.median_message_interval_seconds)}"
            ),
            (
                "Mayor pausa observada: "
                f"{StatisticsDialog._format_duration(statistics.longest_message_interval_seconds)}"
            ),
            (
                "Tu tiempo típico de respuesta: "
                f"{StatisticsDialog._format_duration(chat.median_my_response_seconds)}"
            ),
            (
                f"Tiempo típico de respuesta {'del grupo' if chat.is_group else 'del contacto'}: "
                f"{StatisticsDialog._format_duration(chat.median_their_response_seconds)}"
            ),
            "",
            "Contenido",
            (
                f"Audios: {chat.audio_messages}; imágenes: {chat.image_messages}; "
                f"videos: {chat.video_messages}; archivos: {chat.file_messages}; "
                f"stickers: {chat.stickers}"
            ),
            "",
            "Tendencia aproximada del lenguaje",
            f"Lectura general: {emotional_reading}",
            StatisticsDialog._emotional_meaning(
                chat.positive_weight,
                chat.negative_weight,
            ),
            StatisticsDialog._emotional_evidence(chat.sentiment_messages, chat.total),
            (
                "Se basa en palabras, emojis, negaciones e intensificadores. No comprende "
                "por completo el contexto, la ironía ni el sarcasmo."
            ),
        ]
        return "\n".join(lines)

    @classmethod
    def _format_participant_detail(cls, participant: ParticipantChatStatistics) -> str:
        emotional_reading = StatisticsDialog._emotional_interpretation(
            participant.positive_weight,
            participant.negative_weight,
        )
        return "\n".join(
            (
                f"Persona: {participant.name}",
                f"Mensajes: {participant.messages}",
                f"Hora con más actividad: {cls._format_hour(participant.busiest_hour)}",
                (
                    "Intervalo típico entre sus mensajes: "
                    f"{StatisticsDialog._format_duration(participant.median_interval_seconds)}"
                ),
                f"Tendencia del lenguaje: {emotional_reading}",
                StatisticsDialog._emotional_meaning(
                    participant.positive_weight,
                    participant.negative_weight,
                ),
                StatisticsDialog._emotional_evidence(
                    participant.sentiment_messages,
                    participant.messages,
                ),
                (
                    "Se basa en palabras, emojis, negaciones e intensificadores. No comprende "
                    "por completo el contexto, la ironía ni el sarcasmo."
                ),
            )
        )

    @staticmethod
    def _format_hour(hour: int | None) -> str:
        if hour is None:
            return "sin datos"
        return f"{hour:02d}:00 a {hour:02d}:59"

    @classmethod
    def _format_peak_hours(cls, hourly_activity: tuple[tuple[int, int], ...]) -> str:
        if not hourly_activity:
            return "sin datos"
        peaks = sorted(hourly_activity, key=lambda item: (-item[1], item[0]))[:3]
        return ", ".join(
            f"{cls._format_hour(hour)} ({count} mensajes)" for hour, count in peaks
        )

    def _show_participant_detail(self, participant: ParticipantChatStatistics) -> None:
        self.participant_detail.ChangeValue(self._format_participant_detail(participant))
        self.participant_detail.SetInsertionPoint(0)

    def _on_participant_selected(self, event: wx.ListEvent) -> None:
        index = event.GetIndex()
        if 0 <= index < len(self._visible_participants):
            self._show_participant_detail(self._visible_participants[index])

    def _on_refresh(self, _event: wx.CommandEvent) -> None:
        self.refresh()

    def _on_period_changed(self, _event: wx.CommandEvent) -> None:
        self.refresh()

    def _on_close_button(self, _event: wx.CommandEvent) -> None:
        self.deactivate()
        self.EndModal(wx.ID_CLOSE)

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.deactivate()
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()

    def _on_close(self, event: wx.CloseEvent) -> None:
        self.deactivate()
        event.Skip()
