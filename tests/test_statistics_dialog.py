from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace

import wx

from cliente_xmpp.models.statistics import (
    ChatMessageStatistics,
    DailyChatMessageStatistics,
    DailyMessageStatistics,
)
from cliente_xmpp.ui.statistics_dialog import StatisticsDialog


class StatisticsDialogFormattingTests(unittest.TestCase):
    def test_chat_detail_explains_activity_and_emotional_meter(self) -> None:
        chat = ChatMessageStatistics(
            chat_jid="friend@example.test",
            name="Amistad",
            is_group=False,
            sent=8,
            received=12,
            current_received_streak=3,
            maximum_received_streak=5,
            current_sent_streak=0,
            median_my_response_seconds=120,
            median_their_response_seconds=300,
            active_days=4,
            first_message_at=datetime(2026, 7, 10, 12, tzinfo=UTC),
            last_message_at=datetime(2026, 7, 19, 12, tzinfo=UTC),
            busiest_hour=20,
            stickers=2,
            audio_messages=1,
            image_messages=3,
            video_messages=0,
            file_messages=1,
            positive_weight=9.0,
            negative_weight=3.0,
            sentiment_messages=6,
        )

        detail = StatisticsDialog._format_chat_detail(chat)

        self.assertIn("Mensajes totales: 20", detail)
        self.assertIn("Mensajes pendientes de tu respuesta: 3", detail)
        self.assertIn("Tendencia: Mayormente positiva", detail)
        self.assertIn("hay una mayoría clara", detail)
        self.assertIn("alegría, afecto, agradecimiento", detail)
        self.assertIn("no califica a la persona ni a la relación", detail)
        self.assertIn("Evidencia limitada", detail)
        self.assertNotIn("Peso positivo", detail)
        self.assertNotIn("de -100", detail)
        self.assertIn("Tiempo típico de respuesta del contacto: 5 minutos", detail)
        self.assertIn(
            "Se basa en palabras, emojis, negaciones e intensificadores.",
            detail,
        )
        self.assertIn("No comprende por completo el contexto, la ironía ni el sarcasmo", detail)
        self.assertEqual(
            StatisticsDialog._format_emotional_load(chat),
            "Mayormente positiva",
        )

    def test_emotional_meaning_explains_negative_and_balanced_language(self) -> None:
        negative = StatisticsDialog._emotional_meaning(2.0, 8.0)
        balanced = StatisticsDialog._emotional_meaning(5.0, 5.0)

        self.assertIn("mayoría clara", negative)
        self.assertIn("tristeza, enojo, preocupación", negative)
        self.assertIn("sin un predominio claro", balanced)

    def test_escape_deactivates_and_closes_statistics_dialog(self) -> None:
        calls: list[object] = []
        dialog = SimpleNamespace(
            deactivate=lambda: calls.append("deactivate"),
            EndModal=lambda result: calls.append(result),
        )
        event = SimpleNamespace(GetKeyCode=lambda: wx.WXK_ESCAPE, Skip=lambda: calls.append("skip"))

        StatisticsDialog._on_key_down(dialog, event)

        self.assertEqual(calls, ["deactivate", wx.ID_CANCEL])

    def test_day_detail_includes_extremes_counts_and_media_by_chat(self) -> None:
        day = DailyMessageStatistics(
            day=date(2026, 7, 19),
            sent=7,
            received=5,
            chats=(
                DailyChatMessageStatistics(
                    chat_jid="friend@example.test",
                    name="Amistad",
                    is_group=False,
                    sent=5,
                    received=1,
                    stickers=1,
                    audio_messages=2,
                    image_messages=0,
                    video_messages=0,
                    file_messages=0,
                ),
                DailyChatMessageStatistics(
                    chat_jid="#group@example.test",
                    name="Grupo de prueba",
                    is_group=True,
                    sent=2,
                    received=4,
                    stickers=0,
                    audio_messages=0,
                    image_messages=1,
                    video_messages=1,
                    file_messages=1,
                ),
            ),
        )

        detail = StatisticsDialog._format_day_detail(day)

        self.assertIn("Mensajes totales: 12", detail)
        self.assertIn("Más mensajes enviados a: Amistad, 5", detail)
        self.assertIn("Más mensajes recibidos de: Grupo de prueba, 4", detail)
        self.assertIn("Audios: 2", detail)
        self.assertIn("Imágenes: 1", detail)
        self.assertIn("Archivos: 1", detail)
        self.assertIn("Stickers: 1", detail)
        self.assertIn("5 enviados, 1 recibidos, 6 total", detail)


if __name__ == "__main__":
    unittest.main()
