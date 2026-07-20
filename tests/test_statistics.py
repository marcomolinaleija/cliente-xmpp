from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.storage.message_store import MessageStore


class MessageStatisticsTests(unittest.TestCase):
    def test_statistics_count_days_chats_streaks_and_media(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            account_jid = "me@example.test"
            first_chat = "friend@example.test"
            second_chat = "#group@example.test"
            store.upsert_chats(
                account_jid,
                [
                    Chat(jid=first_chat, name="Amistad"),
                    Chat(jid=second_chat, name="Grupo de prueba", is_group=True),
                ],
            )

            messages = [
                self._message(first_chat, "old", 10, 12, outgoing=False),
                self._message(first_chat, "gracias", 17, 10, outgoing=False),
                self._message(
                    first_chat,
                    "terrible",
                    17,
                    11,
                    outgoing=False,
                    media_kind="audio",
                ),
                self._message(first_chat, "a3", 17, 12, outgoing=True),
                self._message(first_chat, "a4", 17, 13, outgoing=True),
                self._message(first_chat, "a5", 17, 14, outgoing=False, is_sticker=True),
                self._message(second_chat, "b1", 18, 8, outgoing=True),
                self._message(second_chat, "b2", 18, 9, outgoing=True),
                self._message(second_chat, "b3", 18, 10, outgoing=False, media_kind="image"),
                self._message(second_chat, "b4", 18, 11, outgoing=True, media_kind="file"),
                self._message(first_chat, "a6", 19, 9, outgoing=False),
                self._message(first_chat, "a7", 19, 10, outgoing=False),
                self._message(first_chat, "a8", 19, 11, outgoing=False),
            ]
            store.upsert_messages(account_jid, messages)

            statistics = store.load_statistics(
                account_jid,
                7,
                now=datetime(2026, 7, 19, 18, tzinfo=UTC),
            )

            self.assertEqual(statistics.total_sent, 5)
            self.assertEqual(statistics.total_received, 7)
            self.assertEqual(statistics.total, 12)
            self.assertEqual(statistics.active_days, 3)
            self.assertEqual(statistics.calendar_days, 7)
            self.assertEqual(len(statistics.daily), 7)
            self.assertEqual(statistics.audio_messages, 1)
            self.assertEqual(statistics.image_messages, 1)
            self.assertEqual(statistics.file_messages, 1)
            self.assertEqual(statistics.stickers, 1)

            by_day = {item.day.day: item for item in statistics.daily}
            july_17 = by_day[17]
            self.assertEqual((july_17.sent, july_17.received), (2, 3))
            self.assertEqual(len(july_17.chats), 1)
            self.assertEqual(july_17.chats[0].name, "Amistad")
            self.assertEqual(july_17.audio_messages, 1)
            self.assertEqual(july_17.stickers, 1)

            july_18 = by_day[18]
            self.assertEqual((july_18.sent, july_18.received), (3, 1))
            self.assertEqual(july_18.chats[0].name, "Grupo de prueba")
            self.assertEqual(july_18.image_messages, 1)
            self.assertEqual(july_18.file_messages, 1)

            friendship = statistics.chats[0]
            self.assertEqual(friendship.name, "Amistad")
            self.assertEqual(friendship.sent, 2)
            self.assertEqual(friendship.received, 6)
            self.assertEqual(friendship.current_received_streak, 4)
            self.assertEqual(friendship.maximum_received_streak, 4)
            self.assertEqual(friendship.median_my_response_seconds, 3600)
            self.assertEqual(friendship.median_their_response_seconds, 3600)
            self.assertEqual(friendship.active_days, 2)
            self.assertEqual(friendship.audio_messages, 1)
            self.assertEqual(friendship.stickers, 1)
            self.assertEqual(friendship.positive_weight, 1.5)
            self.assertEqual(friendship.negative_weight, 3.0)
            self.assertEqual(friendship.sentiment_messages, 2)
            self.assertAlmostEqual(friendship.emotional_balance, -100 / 3)

            group = statistics.chats[1]
            self.assertTrue(group.is_group)
            self.assertEqual(group.current_sent_streak, 1)
            self.assertEqual(group.maximum_received_streak, 1)
            self.assertEqual(group.image_messages, 1)
            self.assertEqual(group.file_messages, 1)

            self.assertEqual(statistics.positive_weight, 1.5)
            self.assertEqual(statistics.negative_weight, 3.0)
            self.assertEqual(statistics.sentiment_messages, 2)

            individual_statistics = store.load_statistics(
                account_jid,
                7,
                now=datetime(2026, 7, 19, 18, tzinfo=UTC),
                chat_is_group=False,
            )
            self.assertEqual(individual_statistics.total, 8)
            self.assertEqual(
                [chat.name for chat in individual_statistics.chats],
                ["Amistad"],
            )

            group_statistics = store.load_statistics(
                account_jid,
                7,
                now=datetime(2026, 7, 19, 18, tzinfo=UTC),
                chat_is_group=True,
            )
            self.assertEqual(group_statistics.total, 4)
            self.assertEqual(
                [chat.name for chat in group_statistics.chats],
                ["Grupo de prueba"],
            )

    def test_all_history_starts_on_first_local_message_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            account_jid = "me@example.test"
            store.upsert_messages(
                account_jid,
                [
                    self._message("friend@example.test", "first", 10, 12, outgoing=False),
                    self._message("friend@example.test", "last", 19, 12, outgoing=True),
                ],
            )

            statistics = store.load_statistics(
                account_jid,
                None,
                now=datetime(2026, 7, 19, 18, tzinfo=UTC),
            )

            self.assertEqual(statistics.from_date, datetime(2026, 7, 10).date())
            self.assertEqual(statistics.calendar_days, 10)
            self.assertEqual(statistics.active_days, 2)
            self.assertEqual(len(statistics.daily), 2)

    def test_gateway_component_messages_are_excluded_structurally(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            account_jid = "me@example.test"
            component_jid = "whatsapp.example.test"
            contact_jid = f"friend@{component_jid}"
            store.upsert_chats(
                account_jid,
                [
                    Chat(jid=component_jid, name="Administración"),
                    Chat(jid=contact_jid, name="Contacto de ejemplo"),
                ],
            )
            store.upsert_messages(
                account_jid,
                [
                    Message(
                        chat_jid=component_jid,
                        sender_jid=component_jid,
                        body=(
                            "Incoming call from Contacto de ejemplo "
                            f"(xmpp:{contact_jid}) at 2026-07-19 12:00:00+00:00"
                        ),
                        sent_at=datetime(2026, 7, 19, 12, tzinfo=UTC),
                        message_id="gateway-call",
                    ),
                    Message(
                        chat_jid=component_jid,
                        sender_jid=component_jid,
                        body="Otro aviso administrativo",
                        sent_at=datetime(2026, 7, 19, 13, tzinfo=UTC),
                        message_id="gateway-notice",
                    ),
                    Message(
                        chat_jid=contact_jid,
                        sender_jid=contact_jid,
                        body="Hola",
                        sent_at=datetime(2026, 7, 19, 14, tzinfo=UTC),
                        message_id="contact-message",
                    ),
                ],
            )

            statistics = store.load_statistics(
                account_jid,
                7,
                now=datetime(2026, 7, 19, 18, tzinfo=UTC),
            )

            self.assertEqual(statistics.total, 1)
            self.assertEqual(len(statistics.chats), 1)
            self.assertEqual(statistics.chats[0].chat_jid, contact_jid)

    def test_local_chat_statistics_include_people_intervals_peaks_and_phrases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            account_jid = "me@example.test"
            chat_jid = "#group@example.test"
            store.upsert_chat(
                account_jid,
                Chat(jid=chat_jid, name="Grupo de prueba", is_group=True),
            )
            store.upsert_messages(
                account_jid,
                [
                    Message(
                        chat_jid=chat_jid,
                        sender_jid=account_jid,
                        body="Buenos días equipo",
                        sent_at=datetime(2026, 7, 19, 8, tzinfo=UTC),
                        outgoing=True,
                        message_id="message-1",
                        chat_is_group=True,
                    ),
                    Message(
                        chat_jid=chat_jid,
                        sender_jid="ana@example.test",
                        sender_name="Ana",
                        body="Buenos días equipo",
                        sent_at=datetime(2026, 7, 19, 9, tzinfo=UTC),
                        message_id="message-2",
                        chat_is_group=True,
                    ),
                    Message(
                        chat_jid=chat_jid,
                        sender_jid="ana@example.test",
                        sender_name="Ana",
                        body="Buenos días equipo, excelente trabajo",
                        sent_at=datetime(2026, 7, 19, 9, 30, tzinfo=UTC),
                        message_id="message-3",
                        chat_is_group=True,
                    ),
                    Message(
                        chat_jid=chat_jid,
                        sender_jid="bob@example.test",
                        sender_name="Bob",
                        body="Buenos días equipo",
                        sent_at=datetime(2026, 7, 19, 10, tzinfo=UTC),
                        message_id="message-4",
                        chat_is_group=True,
                    ),
                ],
            )

            statistics = store.load_chat_statistics(
                account_jid,
                chat_jid,
                7,
                now=datetime(2026, 7, 19, 18, tzinfo=UTC),
            )

            self.assertIsNotNone(statistics.overview)
            self.assertEqual(statistics.overview.total, 4)  # type: ignore[union-attr]
            self.assertEqual(
                [(person.name, person.messages) for person in statistics.participants],
                [("Ana", 2), ("Bob", 1), ("Tú", 1)],
            )
            self.assertEqual(statistics.median_message_interval_seconds, 1800)
            self.assertIn((9, 2), statistics.hourly_activity)
            phrases = {
                phrase.phrase: phrase.occurrences
                for phrase in statistics.recurrent_phrases
            }
            self.assertEqual(phrases["buenos días equipo"], 4)

    def test_local_chat_statistics_exclude_zapia_transcriptions_from_phrases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            account_jid = "me@example.test"
            chat_jid = "friend@example.test"
            marker = "Transcrito gratis por zapia.com/app"
            store.upsert_messages(
                account_jid,
                [
                    Message(
                        chat_jid=chat_jid,
                        sender_jid=chat_jid,
                        body="Planeamos viaje pronto",
                        sent_at=datetime(2026, 7, 19, 8, tzinfo=UTC),
                        message_id="normal-1",
                    ),
                    Message(
                        chat_jid=chat_jid,
                        sender_jid=chat_jid,
                        body="Planeamos viaje mañana",
                        sent_at=datetime(2026, 7, 19, 9, tzinfo=UTC),
                        message_id="normal-2",
                    ),
                    Message(
                        chat_jid=chat_jid,
                        sender_jid=chat_jid,
                        body=f"{marker} llamada sobre el trabajo",
                        sent_at=datetime(2026, 7, 19, 10, tzinfo=UTC),
                        message_id="zapia-1",
                    ),
                    Message(
                        chat_jid=chat_jid,
                        sender_jid=chat_jid,
                        body=f"{marker} llamada sobre la familia",
                        sent_at=datetime(2026, 7, 19, 11, tzinfo=UTC),
                        message_id="zapia-2",
                    ),
                ],
            )

            statistics = store.load_chat_statistics(
                account_jid,
                chat_jid,
                7,
                now=datetime(2026, 7, 19, 18, tzinfo=UTC),
            )

            phrases = {phrase.phrase for phrase in statistics.recurrent_phrases}
            self.assertIn("planeamos viaje", phrases)
            self.assertFalse(any("zapia" in phrase for phrase in phrases))

    @staticmethod
    def _message(
        chat_jid: str,
        message_id: str,
        day: int,
        hour: int,
        *,
        outgoing: bool,
        media_kind: str = "",
        is_sticker: bool = False,
    ) -> Message:
        return Message(
            chat_jid=chat_jid,
            sender_jid="me@example.test" if outgoing else "sender@example.test",
            body=message_id,
            sent_at=datetime(2026, 7, day, hour, tzinfo=UTC),
            outgoing=outgoing,
            media_kind=media_kind or ("image" if is_sticker else ""),
            is_sticker=is_sticker,
            message_id=message_id,
            chat_is_group=chat_jid.startswith("#"),
        )


if __name__ == "__main__":
    unittest.main()
