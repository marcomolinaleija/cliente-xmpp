from __future__ import annotations

import unittest
from datetime import UTC, datetime

from cliente_xmpp.models.statistics import ChatMessageStatistics
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
        self.assertIn("Peso positivo: 9.0", detail)
        self.assertIn("Peso negativo: 3.0", detail)
        self.assertIn("Medidor de balance: +50", detail)
        self.assertIn("ironía o sarcasmo", detail)
        self.assertEqual(StatisticsDialog._format_emotional_load(chat), "Positiva, +6.0")


if __name__ == "__main__":
    unittest.main()

