from __future__ import annotations

from collections.abc import Callable

import wx

from cliente_xmpp.models.statistics import ChatMessageStatistics, MessageStatistics
from cliente_xmpp.ui.theme import apply_theme

StatisticsLoadedCallback = Callable[[MessageStatistics | None, str], None]
StatisticsLoader = Callable[[int | None, StatisticsLoadedCallback], None]

PERIODS: tuple[tuple[str, int | None], ...] = (
    ("Últimos 7 días", 7),
    ("Últimos 30 días", 30),
    ("Últimos 90 días", 90),
    ("Todo el historial local", None),
)
CHAT_SORTS = (
    "Mayor actividad",
    "Mayor carga positiva",
    "Mayor carga negativa",
)


class StatisticsDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, loader: StatisticsLoader) -> None:
        super().__init__(
            parent,
            title="Estadísticas de mensajes",
            size=(940, 680),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._loader = loader
        self._active = True
        self._request_id = 0
        self._statistics: MessageStatistics | None = None
        self._visible_chats: tuple[ChatMessageStatistics, ...] = ()

        period_label = wx.StaticText(self, label="Período:")
        self.period_choice = wx.Choice(self, choices=[label for label, _days in PERIODS])
        self.period_choice.SetSelection(1)
        self.period_choice.SetName("Período de las estadísticas")
        self.refresh_button = wx.Button(self, label="&Actualizar")

        self.status = wx.StaticText(
            self,
            label="Las estadísticas se calculan con los mensajes guardados localmente.",
        )
        self.status.SetName("Estado de las estadísticas")

        self.notebook = wx.Notebook(self)
        self.notebook.SetName("Secciones de estadísticas")
        self.summary = self._create_summary_page()
        self.daily = self._create_daily_page()
        self.chats = self._create_chats_page()
        self.unanswered = self._create_unanswered_page()

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
        self.Bind(wx.EVT_CHOICE, self._on_chat_sort_changed, self.chat_sort_choice)
        self.chats.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_chat_selected)
        self.Bind(wx.EVT_BUTTON, self._on_close_button, close_button)
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
        self.status.SetLabel("Calculando estadísticas...")
        self.summary.ChangeValue("Calculando estadísticas. Espera por favor...")
        self.summary.SetInsertionPoint(0)

        def loaded(statistics: MessageStatistics | None, error: str) -> None:
            self._finish_load(request_id, statistics, error)

        self._loader(period_days, loaded)

    def _create_summary_page(self) -> wx.TextCtrl:
        page = wx.Panel(self.notebook)
        summary = wx.TextCtrl(
            page,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_NONE,
        )
        summary.SetName("Resumen de estadísticas")
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(summary, 1, wx.ALL | wx.EXPAND, 12)
        page.SetSizer(box)
        self.notebook.AddPage(page, "Resumen")
        return summary

    def _create_daily_page(self) -> wx.ListCtrl:
        page = wx.Panel(self.notebook)
        message = wx.StaticText(
            page,
            label="Actividad por fecha; los días más recientes aparecen primero.",
        )
        daily = self._create_list(page, "Mensajes por día")
        for index, (label, width) in enumerate(
            (("Fecha", 180), ("Enviados", 140), ("Recibidos", 140), ("Total", 140))
        ):
            daily.InsertColumn(index, label, width=width)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(message, 0, wx.ALL | wx.EXPAND, 10)
        box.Add(daily, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        page.SetSizer(box)
        self.notebook.AddPage(page, "Por día")
        return daily

    def _create_chats_page(self) -> wx.ListCtrl:
        page = wx.Panel(self.notebook)
        message = wx.StaticText(
            page,
            label=(
                "Selecciona una conversación para consultar sus estadísticas. La carga "
                "emocional es una estimación local basada en palabras y emojis."
            ),
        )
        sort_label = wx.StaticText(page, label="Ordenar por:")
        self.chat_sort_choice = wx.Choice(page, choices=list(CHAT_SORTS))
        self.chat_sort_choice.SetSelection(0)
        self.chat_sort_choice.SetName("Orden de las conversaciones")
        chats = self._create_list(page, "Actividad por conversación")
        for index, (label, width) in enumerate(
            (
                ("Conversación", 245),
                ("Tipo", 85),
                ("Enviados", 85),
                ("Recibidos", 85),
                ("Total", 75),
                ("Parte enviada", 115),
                ("Carga emocional", 160),
            )
        ):
            chats.InsertColumn(index, label, width=width)

        self.chat_detail = wx.TextCtrl(
            page,
            value="Selecciona una conversación para ver el detalle.",
            style=wx.TE_MULTILINE | wx.TE_READONLY,
        )
        self.chat_detail.SetName("Detalle de la conversación seleccionada")
        detail_box = wx.StaticBoxSizer(
            wx.VERTICAL,
            page,
            "Estadísticas de la conversación seleccionada",
        )
        detail_box.Add(self.chat_detail, 1, wx.ALL | wx.EXPAND, 8)

        sorting = wx.BoxSizer(wx.HORIZONTAL)
        sorting.Add(sort_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        sorting.Add(self.chat_sort_choice, 0)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(message, 0, wx.ALL | wx.EXPAND, 8)
        box.Add(sorting, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 8)
        box.Add(chats, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 8)
        box.Add(detail_box, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 8)
        page.SetSizer(box)
        self.notebook.AddPage(page, "Conversaciones")
        return chats

    def _create_unanswered_page(self) -> wx.ListCtrl:
        page = wx.Panel(self.notebook)
        message = wx.StaticText(
            page,
            label=(
                "Pendientes son los mensajes recibidos al final de la conversación sin un "
                "envío tuyo posterior. Máxima es la mayor racha observada en el período."
            ),
        )
        unanswered = self._create_list(page, "Rachas y respuestas pendientes")
        for index, (label, width) in enumerate(
            (
                ("Conversación", 290),
                ("Tipo", 100),
                ("Pendientes tuyos", 145),
                ("Máxima recibida", 145),
                ("Esperando de ellos", 155),
            )
        ):
            unanswered.InsertColumn(index, label, width=width)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(message, 0, wx.ALL | wx.EXPAND, 10)
        box.Add(unanswered, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        page.SetSizer(box)
        self.notebook.AddPage(page, "Sin respuesta")
        return unanswered

    @staticmethod
    def _create_list(parent: wx.Window, name: str) -> wx.ListCtrl:
        control = wx.ListCtrl(
            parent,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
        )
        control.SetName(name)
        return control

    def _finish_load(
        self,
        request_id: int,
        statistics: MessageStatistics | None,
        error: str,
    ) -> None:
        if not self._active or request_id != self._request_id:
            return
        self.refresh_button.Enable(True)
        self.period_choice.Enable(True)
        if error or statistics is None:
            detail = error or "No se pudieron calcular las estadísticas."
            self.status.SetLabel(detail)
            self.summary.ChangeValue(detail)
            self.summary.SetInsertionPoint(0)
            return

        self._render(statistics)

    def _render(self, statistics: MessageStatistics) -> None:
        self.summary.Freeze()
        self.daily.Freeze()
        self.chats.Freeze()
        self.chat_detail.Freeze()
        self.unanswered.Freeze()
        try:
            self.summary.ChangeValue(self._format_summary(statistics))
            self.summary.SetInsertionPoint(0)

            self.daily.DeleteAllItems()
            for item in reversed(statistics.daily):
                index = self.daily.InsertItem(
                    self.daily.GetItemCount(),
                    item.day.strftime("%d/%m/%Y"),
                )
                self.daily.SetItem(index, 1, str(item.sent))
                self.daily.SetItem(index, 2, str(item.received))
                self.daily.SetItem(index, 3, str(item.total))

            self._statistics = statistics
            self._render_chat_rows()

            self.unanswered.DeleteAllItems()
            unanswered_chats = sorted(
                statistics.chats,
                key=lambda chat: (
                    -chat.current_received_streak,
                    -chat.maximum_received_streak,
                    -chat.received,
                    chat.name.casefold(),
                ),
            )
            for chat in unanswered_chats:
                index = self.unanswered.InsertItem(self.unanswered.GetItemCount(), chat.name)
                self.unanswered.SetItem(index, 1, "Grupo" if chat.is_group else "Contacto")
                self.unanswered.SetItem(index, 2, str(chat.current_received_streak))
                self.unanswered.SetItem(index, 3, str(chat.maximum_received_streak))
                self.unanswered.SetItem(index, 4, str(chat.current_sent_streak))
        finally:
            self.summary.Thaw()
            self.daily.Thaw()
            self.chats.Thaw()
            self.chat_detail.Thaw()
            self.unanswered.Thaw()

        self.status.SetLabel(
            f"{statistics.total} mensajes en {len(statistics.chats)} conversaciones."
        )

    def _render_chat_rows(self, selected_jid: str = "") -> None:
        if self._statistics is None:
            return
        chats = self._sorted_chats(self._statistics.chats)
        self._visible_chats = tuple(chats)
        self.chats.DeleteAllItems()
        selected_index = 0
        for chat in chats:
            index = self.chats.InsertItem(self.chats.GetItemCount(), chat.name)
            self.chats.SetItem(index, 1, "Grupo" if chat.is_group else "Contacto")
            self.chats.SetItem(index, 2, str(chat.sent))
            self.chats.SetItem(index, 3, str(chat.received))
            self.chats.SetItem(index, 4, str(chat.total))
            sent_share = round((chat.sent / chat.total) * 100) if chat.total else 0
            self.chats.SetItem(index, 5, f"{sent_share} %")
            self.chats.SetItem(index, 6, self._format_emotional_load(chat))
            if chat.chat_jid == selected_jid:
                selected_index = index

        if chats:
            self.chats.Select(selected_index)
            self.chats.Focus(selected_index)
            self._show_chat_detail(chats[selected_index])
        else:
            self.chat_detail.ChangeValue("No hay conversaciones para este período.")
            self.chat_detail.SetInsertionPoint(0)

    def _sorted_chats(
        self,
        chats: tuple[ChatMessageStatistics, ...],
    ) -> list[ChatMessageStatistics]:
        selection = self.chat_sort_choice.GetSelection()
        if selection == 1:
            return sorted(
                chats,
                key=lambda chat: (-chat.positive_weight, chat.negative_weight, -chat.total),
            )
        if selection == 2:
            return sorted(
                chats,
                key=lambda chat: (-chat.negative_weight, chat.positive_weight, -chat.total),
            )
        return sorted(
            chats,
            key=lambda chat: (-chat.total, chat.name.casefold(), chat.chat_jid),
        )

    def _show_chat_detail(self, chat: ChatMessageStatistics) -> None:
        self.chat_detail.ChangeValue(self._format_chat_detail(chat))
        self.chat_detail.SetInsertionPoint(0)

    @classmethod
    def _format_chat_detail(cls, chat: ChatMessageStatistics) -> str:
        sent_share = (chat.sent / chat.total) * 100 if chat.total else 0.0
        daily_average = chat.total / max(1, chat.active_days)
        lines = [
            f"Conversación: {chat.name}",
            f"Tipo: {'grupo' if chat.is_group else 'contacto individual'}",
            "",
            "Actividad",
            f"Mensajes totales: {chat.total}",
            f"Enviados por ti: {chat.sent}",
            f"Recibidos: {chat.received}",
            f"Participación tuya: {sent_share:.1f} %",
            f"Días con actividad: {chat.active_days}",
            f"Promedio por día activo: {daily_average:.1f}",
            f"Primera actividad del período: {cls._format_datetime(chat.first_message_at)}",
            f"Última actividad del período: {cls._format_datetime(chat.last_message_at)}",
        ]
        if chat.busiest_hour is not None:
            lines.append(
                f"Hora con más actividad: {chat.busiest_hour:02d}:00 a "
                f"{chat.busiest_hour:02d}:59"
            )

        lines.extend(
            (
                "",
                "Rachas y respuestas",
                f"Mensajes pendientes de tu respuesta: {chat.current_received_streak}",
                f"Mayor racha recibida sin respuesta: {chat.maximum_received_streak}",
                f"Mensajes tuyos esperando respuesta: {chat.current_sent_streak}",
                (
                    "Tu tiempo típico de respuesta: "
                    f"{cls._format_duration(chat.median_my_response_seconds)}"
                ),
                (
                    "Tiempo típico de respuesta del chat: "
                    f"{cls._format_duration(chat.median_their_response_seconds)}"
                ),
                "",
                "Contenido multimedia",
                f"Audios: {chat.audio_messages}",
                f"Imágenes: {chat.image_messages}",
                f"Videos: {chat.video_messages}",
                f"Archivos: {chat.file_messages}",
                f"Stickers: {chat.stickers}",
                "",
                "Medidor emocional aproximado",
                f"Peso positivo: {chat.positive_weight:.1f}",
                f"Peso negativo: {chat.negative_weight:.1f}",
                f"Carga neta: {chat.emotional_net:+.1f}",
                (
                    f"Medidor de balance: {chat.emotional_balance:+.0f} de -100 "
                    "(negativo) a +100 (positivo)"
                ),
                f"Mensajes con indicadores detectados: {chat.sentiment_messages}",
                (
                    "Esta medición se procesa localmente y sólo detecta palabras y emojis; "
                    "puede equivocarse con contexto, ironía o sarcasmo."
                ),
            )
        )
        if chat.is_group:
            lines.append(
                "En grupos, las rachas y respuestas combinan mensajes de varios participantes."
            )
        return "\n".join(lines)

    @staticmethod
    def _format_emotional_load(chat: ChatMessageStatistics) -> str:
        if chat.positive_weight + chat.negative_weight == 0:
            return "Sin datos"
        if abs(chat.emotional_net) < 0.05:
            return "Equilibrada, 0.0"
        if chat.emotional_net > 0:
            return f"Positiva, {chat.emotional_net:+.1f}"
        return f"Negativa, {chat.emotional_net:+.1f}"

    @staticmethod
    def _format_datetime(value: object) -> str:
        try:
            return value.astimezone().strftime("%d/%m/%Y, %H:%M")
        except (AttributeError, OSError, ValueError):
            return "sin datos"

    @classmethod
    def _format_summary(cls, statistics: MessageStatistics) -> str:
        if statistics.total == 0:
            return (
                "No hay mensajes guardados para este período.\n\n"
                "Las estadísticas usan la caché local; un historial remoto que todavía no se "
                "haya descargado no se incluye."
            )

        average_sent = statistics.total_sent / max(1, statistics.calendar_days)
        average_received = statistics.total_received / max(1, statistics.calendar_days)
        most_active = max(statistics.chats, key=lambda chat: chat.total)
        least_active = min(statistics.chats, key=lambda chat: chat.total)
        most_received = max(statistics.chats, key=lambda chat: chat.received)
        longest_streak = max(
            statistics.chats,
            key=lambda chat: (chat.maximum_received_streak, chat.received),
        )
        currently_unanswered = max(
            statistics.chats,
            key=lambda chat: (chat.current_received_streak, chat.received),
        )
        waiting_for_reply = max(
            statistics.chats,
            key=lambda chat: (chat.current_sent_streak, chat.sent),
        )
        busiest_day = max(statistics.daily, key=lambda day: day.total)
        balanced_candidates = [
            chat
            for chat in statistics.chats
            if chat.sent > 0 and chat.received > 0 and chat.total >= 4
        ]
        most_balanced = (
            min(
                balanced_candidates,
                key=lambda chat: (abs(chat.sent - chat.received) / chat.total, -chat.total),
            )
            if balanced_candidates
            else None
        )
        most_positive = max(statistics.chats, key=lambda chat: chat.positive_weight)
        most_negative = max(statistics.chats, key=lambda chat: chat.negative_weight)
        overall_emotional_balance = cls._emotional_balance(
            statistics.positive_weight,
            statistics.negative_weight,
        )

        lines = [
            cls._period_label(statistics),
            "",
            f"Mensajes totales: {statistics.total}",
            f"Enviados por ti: {statistics.total_sent}",
            f"Recibidos: {statistics.total_received}",
            f"Promedio diario: {average_sent:.1f} enviados y {average_received:.1f} recibidos",
            f"Días con actividad: {statistics.active_days} de {statistics.calendar_days}",
            f"Conversaciones activas: {len(statistics.chats)}",
            "",
            "Destacados",
            f"Más actividad: {most_active.name}, {most_active.total} mensajes",
            f"Menos actividad: {least_active.name}, {least_active.total} mensajes",
            f"Quien más te escribe: {most_received.name}, {most_received.received} mensajes",
            (
                f"Día con más actividad: {busiest_day.day.strftime('%d/%m/%Y')}, "
                f"{busiest_day.total} mensajes"
            ),
        ]
        if most_balanced is not None:
            lines.append(
                f"Conversación más equilibrada: {most_balanced.name}, "
                f"{most_balanced.sent} enviados y {most_balanced.received} recibidos"
            )
        if longest_streak.maximum_received_streak:
            lines.append(
                f"Mayor racha recibida sin respuesta: {longest_streak.name}, "
                f"{longest_streak.maximum_received_streak} mensajes"
            )
        if currently_unanswered.current_received_streak:
            lines.append(
                f"Más mensajes pendientes de tu respuesta: {currently_unanswered.name}, "
                f"{currently_unanswered.current_received_streak}"
            )
        else:
            lines.append("Mensajes pendientes de tu respuesta: ninguno")
        if waiting_for_reply.current_sent_streak:
            lines.append(
                f"Donde más esperas respuesta: {waiting_for_reply.name}, "
                f"{waiting_for_reply.current_sent_streak} mensajes tuyos consecutivos"
            )
        if statistics.busiest_hour is not None:
            lines.append(
                f"Hora con más actividad: {statistics.busiest_hour:02d}:00 a "
                f"{statistics.busiest_hour:02d}:59"
            )
        if most_positive.positive_weight > 0:
            lines.append(
                f"Mayor carga positiva: {most_positive.name}, "
                f"peso {most_positive.positive_weight:.1f}"
            )
        if most_negative.negative_weight > 0:
            lines.append(
                f"Mayor carga negativa: {most_negative.name}, "
                f"peso {most_negative.negative_weight:.1f}"
            )

        lines.extend(
            (
                "",
                "Respuestas",
                (
                    "Tu tiempo típico de respuesta en chats individuales: "
                    f"{cls._format_duration(statistics.median_my_response_seconds)}"
                ),
                (
                    "Tiempo típico de respuesta de tus contactos: "
                    f"{cls._format_duration(statistics.median_their_response_seconds)}"
                ),
                "",
                "Contenido multimedia",
                f"Audios: {statistics.audio_messages}",
                f"Imágenes: {statistics.image_messages}",
                f"Videos: {statistics.video_messages}",
                f"Archivos: {statistics.file_messages}",
                f"Stickers: {statistics.stickers}",
                "",
                "Medidor emocional aproximado",
                f"Peso positivo: {statistics.positive_weight:.1f}",
                f"Peso negativo: {statistics.negative_weight:.1f}",
                (
                    f"Balance general: {overall_emotional_balance:+.0f} de -100 a +100"
                ),
                f"Mensajes con indicadores detectados: {statistics.sentiment_messages}",
                "",
                (
                    "Nota: se usa el historial guardado localmente. Los grupos se muestran como "
                    "conversaciones y sus rachas pueden incluir mensajes de varios participantes."
                ),
                (
                    "Los mensajes administrativos enviados por el componente del puente, como "
                    "los avisos de llamadas, no se incluyen."
                ),
            )
        )
        return "\n".join(lines)

    @staticmethod
    def _emotional_balance(positive: float, negative: float) -> float:
        total = positive + negative
        if total == 0:
            return 0.0
        return ((positive - negative) / total) * 100

    @staticmethod
    def _period_label(statistics: MessageStatistics) -> str:
        if statistics.from_date is None:
            return "Período: sin mensajes"
        return (
            f"Período: {statistics.from_date.strftime('%d/%m/%Y')} a "
            f"{statistics.to_date.strftime('%d/%m/%Y')}"
        )

    @staticmethod
    def _format_duration(seconds: float | None) -> str:
        if seconds is None:
            return "sin datos suficientes"
        total_seconds = max(0, round(seconds))
        if total_seconds < 60:
            return f"{total_seconds} segundos"
        if total_seconds < 3600:
            return f"{round(total_seconds / 60)} minutos"
        if total_seconds < 86400:
            hours = total_seconds / 3600
            return f"{hours:.1f} horas"
        days = total_seconds / 86400
        return f"{days:.1f} días"

    def _on_refresh(self, _event: wx.CommandEvent) -> None:
        self.refresh()

    def _on_period_changed(self, _event: wx.CommandEvent) -> None:
        self.refresh()

    def _on_chat_sort_changed(self, _event: wx.CommandEvent) -> None:
        selected_jid = ""
        selected_index = self.chats.GetFirstSelected()
        if 0 <= selected_index < len(self._visible_chats):
            selected_jid = self._visible_chats[selected_index].chat_jid
        self.chats.Freeze()
        try:
            self._render_chat_rows(selected_jid)
        finally:
            self.chats.Thaw()

    def _on_chat_selected(self, event: wx.ListEvent) -> None:
        index = event.GetIndex()
        if 0 <= index < len(self._visible_chats):
            self._show_chat_detail(self._visible_chats[index])

    def _on_close_button(self, _event: wx.CommandEvent) -> None:
        self.deactivate()
        self.EndModal(wx.ID_CLOSE)

    def _on_close(self, event: wx.CloseEvent) -> None:
        self.deactivate()
        event.Skip()
