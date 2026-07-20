from __future__ import annotations

from collections.abc import Callable

import wx

from cliente_xmpp.models.statistics import (
    ChatMessageStatistics,
    DailyMessageStatistics,
    MessageStatistics,
)
from cliente_xmpp.ui.theme import apply_theme

StatisticsLoadedCallback = Callable[[MessageStatistics | None, str], None]
StatisticsLoader = Callable[[int | None, bool | None, StatisticsLoadedCallback], None]

PERIODS: tuple[tuple[str, int | None], ...] = (
    ("Últimos 7 días", 7),
    ("Últimos 30 días", 30),
    ("Últimos 90 días", 90),
    ("Todo el historial local", None),
)
CHAT_SORTS = (
    "Mayor actividad",
    "Lenguaje más positivo",
    "Lenguaje más negativo",
)
CHAT_FILTERS: tuple[tuple[str, bool | None], ...] = (
    ("Todos los chats", None),
    ("Chats individuales", False),
    ("Grupos", True),
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
        self._visible_days: tuple[DailyMessageStatistics, ...] = ()
        self._visible_chats: tuple[ChatMessageStatistics, ...] = ()

        period_label = wx.StaticText(self, label="Período:")
        self.period_choice = wx.Choice(self, choices=[label for label, _days in PERIODS])
        self.period_choice.SetSelection(1)
        self.period_choice.SetName("Período de las estadísticas")
        chat_filter_label = wx.StaticText(self, label="Chats:")
        self.chat_filter_choice = wx.Choice(
            self,
            choices=[label for label, _is_group in CHAT_FILTERS],
        )
        self.chat_filter_choice.SetSelection(0)
        self.chat_filter_choice.SetName("Filtro de tipo de conversación")
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
        filters.Add(chat_filter_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        filters.Add(self.chat_filter_choice, 0, wx.RIGHT, 8)
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
        self.Bind(wx.EVT_CHOICE, self._on_chat_filter_changed, self.chat_filter_choice)
        self.Bind(wx.EVT_CHOICE, self._on_chat_sort_changed, self.chat_sort_choice)
        self.daily.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_day_selected)
        self.chats.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_chat_selected)
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
        chat_is_group = CHAT_FILTERS[self.chat_filter_choice.GetSelection()][1]
        self.refresh_button.Enable(False)
        self.status.SetLabel("Calculando estadísticas...")
        self.summary.ChangeValue("Calculando estadísticas. Espera por favor...")
        self.summary.SetInsertionPoint(0)

        def loaded(statistics: MessageStatistics | None, error: str) -> None:
            self._finish_load(request_id, statistics, error)

        self._loader(period_days, chat_is_group, loaded)

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

        self.daily_detail = wx.TextCtrl(
            page,
            value="Selecciona un día para ver el detalle.",
            style=wx.TE_MULTILINE | wx.TE_READONLY,
        )
        self.daily_detail.SetName("Detalle del día seleccionado")
        detail_box = wx.StaticBoxSizer(
            wx.VERTICAL,
            page,
            "Estadísticas del día seleccionado",
        )
        detail_box.Add(self.daily_detail, 1, wx.ALL | wx.EXPAND, 8)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(message, 0, wx.ALL | wx.EXPAND, 10)
        box.Add(daily, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        box.Add(detail_box, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        page.SetSizer(box)
        self.notebook.AddPage(page, "Por día")
        return daily

    def _create_chats_page(self) -> wx.ListCtrl:
        page = wx.Panel(self.notebook)
        message = wx.StaticText(
            page,
            label=(
                "Selecciona una conversación para consultar sus estadísticas. La tendencia "
                "del lenguaje es una estimación local basada en palabras y emojis."
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
                ("Tendencia del lenguaje", 180),
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
        self.daily_detail.Freeze()
        self.chats.Freeze()
        self.chat_detail.Freeze()
        self.unanswered.Freeze()
        try:
            self.summary.ChangeValue(self._format_summary(statistics))
            self.summary.SetInsertionPoint(0)

            self.daily.DeleteAllItems()
            self._visible_days = tuple(reversed(statistics.daily))
            for item in self._visible_days:
                index = self.daily.InsertItem(
                    self.daily.GetItemCount(),
                    item.day.strftime("%d/%m/%Y"),
                )
                self.daily.SetItem(index, 1, str(item.sent))
                self.daily.SetItem(index, 2, str(item.received))
                self.daily.SetItem(index, 3, str(item.total))
            if self._visible_days:
                self.daily.Select(0)
                self.daily.Focus(0)
                self._show_day_detail(self._visible_days[0])
            else:
                self.daily_detail.ChangeValue("No hay días disponibles para este período.")
                self.daily_detail.SetInsertionPoint(0)

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
            self.daily_detail.Thaw()
            self.chats.Thaw()
            self.chat_detail.Thaw()
            self.unanswered.Thaw()

        chat_filter_label = CHAT_FILTERS[self.chat_filter_choice.GetSelection()][0]
        self.status.SetLabel(
            f"{statistics.total} mensajes en {len(statistics.chats)} conversaciones. "
            f"Filtro: {chat_filter_label}."
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
                    "Tiempo típico de respuesta "
                    f"{'del grupo' if chat.is_group else 'del contacto'}: "
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
                "Tendencia aproximada del lenguaje",
                (
                    "Tendencia: "
                    f"{cls._emotional_interpretation(chat.positive_weight, chat.negative_weight)}"
                ),
                cls._emotional_meaning(chat.positive_weight, chat.negative_weight),
                cls._emotional_evidence(chat.sentiment_messages, chat.total),
                (
                    "Se basa en palabras, emojis, negaciones e intensificadores. No comprende "
                    "por completo el contexto, la ironía ni el sarcasmo."
                ),
            )
        )
        if chat.is_group:
            lines.append(
                "En grupos, las rachas y respuestas combinan mensajes de varios participantes."
            )
        return "\n".join(lines)

    def _show_day_detail(self, day: DailyMessageStatistics) -> None:
        self.daily_detail.ChangeValue(self._format_day_detail(day))
        self.daily_detail.SetInsertionPoint(0)

    @staticmethod
    def _format_day_detail(day: DailyMessageStatistics) -> str:
        date_label = day.day.strftime("%d/%m/%Y")
        if day.total == 0 or not day.chats:
            return f"Fecha: {date_label}\n\nNo hubo mensajes guardados durante este día."

        most_active = max(day.chats, key=lambda chat: chat.total)
        least_active = min(day.chats, key=lambda chat: chat.total)
        most_sent = max(day.chats, key=lambda chat: chat.sent)
        most_received = max(day.chats, key=lambda chat: chat.received)
        lines = [
            f"Fecha: {date_label}",
            "",
            f"Mensajes totales: {day.total}",
            f"Enviados por ti: {day.sent}",
            f"Recibidos: {day.received}",
            f"Conversaciones activas: {len(day.chats)}",
            "",
            "Destacados",
            f"Más actividad: {most_active.name}, {most_active.total} mensajes",
            f"Menos actividad: {least_active.name}, {least_active.total} mensajes",
            f"Más mensajes enviados a: {most_sent.name}, {most_sent.sent}",
            f"Más mensajes recibidos de: {most_received.name}, {most_received.received}",
            "",
            "Contenido multimedia",
            f"Audios: {day.audio_messages}",
            f"Imágenes: {day.image_messages}",
            f"Videos: {day.video_messages}",
            f"Archivos: {day.file_messages}",
            f"Stickers: {day.stickers}",
            "",
            "Desglose por conversación",
        ]
        for chat in day.chats:
            lines.extend(
                (
                    "",
                    f"{chat.name} ({'grupo' if chat.is_group else 'contacto'}):",
                    f"  {chat.sent} enviados, {chat.received} recibidos, {chat.total} total",
                    (
                        f"  Multimedia: {chat.audio_messages} audios, "
                        f"{chat.image_messages} imágenes, {chat.video_messages} videos, "
                        f"{chat.file_messages} archivos y {chat.stickers} stickers"
                    ),
                )
            )
        return "\n".join(lines)

    @staticmethod
    def _format_emotional_load(chat: ChatMessageStatistics) -> str:
        return StatisticsDialog._emotional_interpretation(
            chat.positive_weight,
            chat.negative_weight,
        )

    @staticmethod
    def _emotional_interpretation(positive: float, negative: float) -> str:
        total = positive + negative
        if total <= 0:
            return "Sin señales emocionales claras"
        balance = ((positive - negative) / total) * 100
        magnitude = abs(balance)
        if magnitude < 15:
            return "Equilibrada"
        direction = "positiva" if balance > 0 else "negativa"
        if magnitude < 40:
            return f"Ligeramente {direction}"
        if magnitude < 70:
            return f"Mayormente {direction}"
        return f"Marcadamente {direction}"

    @staticmethod
    def _emotional_evidence(sentiment_messages: int, total_messages: int) -> str:
        if sentiment_messages <= 0:
            return "Evidencia: no se detectaron expresiones emocionales claras."
        if sentiment_messages <= 2:
            level = "muy limitada"
        elif sentiment_messages <= 7:
            level = "limitada"
        else:
            level = "más consistente"
        return (
            f"Evidencia {level}: se detectaron señales en {sentiment_messages} de "
            f"{total_messages} mensajes."
        )

    @classmethod
    def _emotional_meaning(cls, positive: float, negative: float) -> str:
        interpretation = cls._emotional_interpretation(positive, negative)
        if positive + negative <= 0:
            return (
                "Qué significa: no se detectaron suficientes expresiones para reconocer "
                "un predominio positivo o negativo."
            )
        if interpretation == "Equilibrada":
            return (
                "Qué significa: las expresiones asociadas con bienestar o aprobación y las "
                "asociadas con malestar o preocupación aparecen sin un predominio claro."
            )

        direction = "positiva" if positive > negative else "negativa"
        if interpretation.startswith("Ligeramente"):
            strength = "hay una pequeña mayoría"
        elif interpretation.startswith("Mayormente"):
            strength = "hay una mayoría clara"
        else:
            strength = "hay un predominio muy fuerte"
        if direction == "positiva":
            examples = (
                "de expresiones asociadas con alegría, afecto, agradecimiento, aprobación "
                "o tranquilidad"
            )
        else:
            examples = (
                "de expresiones asociadas con tristeza, enojo, preocupación, rechazo, dolor "
                "o problemas"
            )
        return (
            f"Qué significa: {strength} {examples}. Esto describe el lenguaje detectado; "
            "no califica a la persona ni a la relación."
        )

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
        emotional_candidates = [
            chat
            for chat in statistics.chats
            if chat.positive_weight + chat.negative_weight > 0
        ]
        most_positive = (
            max(emotional_candidates, key=lambda chat: chat.emotional_balance)
            if emotional_candidates
            else None
        )
        most_negative = (
            min(emotional_candidates, key=lambda chat: chat.emotional_balance)
            if emotional_candidates
            else None
        )
        overall_emotional_reading = cls._emotional_interpretation(
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
        if most_positive is not None and most_positive.emotional_balance > 0:
            lines.append(
                f"Tendencia más positiva: {most_positive.name}, "
                f"{cls._format_emotional_load(most_positive).casefold()}"
            )
        if most_negative is not None and most_negative.emotional_balance < 0:
            lines.append(
                f"Tendencia más negativa: {most_negative.name}, "
                f"{cls._format_emotional_load(most_negative).casefold()}"
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
                "Tendencia aproximada del lenguaje",
                f"Tendencia general: {overall_emotional_reading}",
                cls._emotional_meaning(
                    statistics.positive_weight,
                    statistics.negative_weight,
                ),
                cls._emotional_evidence(statistics.sentiment_messages, statistics.total),
                (
                    "Se basa en palabras, emojis, negaciones e intensificadores. No comprende "
                    "por completo el contexto, la ironía ni el sarcasmo."
                ),
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

    def _on_chat_filter_changed(self, _event: wx.CommandEvent) -> None:
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

    def _on_day_selected(self, event: wx.ListEvent) -> None:
        index = event.GetIndex()
        if 0 <= index < len(self._visible_days):
            self._show_day_detail(self._visible_days[index])

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
