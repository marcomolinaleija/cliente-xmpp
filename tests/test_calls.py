from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from cliente_xmpp.models.calls import CallEvent
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.xmpp.calls import CALL_NAMESPACE, call_event_from_xml, routed_chat_jid


class CallContractTests(unittest.TestCase):
    account_jid = "owner@example.test"
    component_jid = "whatsapp.example.test"
    contact_jid = "contact@whatsapp.example.test"

    def _event(
        self,
        call_id: str,
        sequence: int,
        state: str,
        *,
        at: datetime,
        direction: str = "incoming",
        kind: str = "voice",
        answered_at: datetime | None = None,
        ended_at: datetime | None = None,
        terminal_reason: str = "",
        chat_jid: str = "",
        duration_seconds: float | None = None,
        outcome: str = "",
        source: str = "",
    ) -> CallEvent:
        return CallEvent(
            call_id=call_id,
            peer_jid="peer@example.test",
            chat_jid=chat_jid,
            direction=direction,
            kind=kind,
            state=state,
            event_timestamp=at,
            answered_at=answered_at,
            ended_at=ended_at,
            terminal_reason=terminal_reason,
            sequence=sequence,
            duration_seconds=duration_seconds,
            outcome=outcome,
            source=source,
        )

    def _message(
        self, event: CallEvent, *, message_id: str, chat_jid: str | None = None
    ) -> Message:
        return Message(
            chat_jid=chat_jid or self.contact_jid,
            sender_jid=self.component_jid,
            body="Synthetic call notice",
            sent_at=event.event_timestamp,
            message_id=message_id,
            call=event,
        )

    def test_parser_requires_a_valid_namespaced_envelope_and_keeps_unknown_values(self) -> None:
        stanza = ET.fromstring(
            f'''<message xmlns="jabber:client"><body>Fallback notice</body>
            <call xmlns="{CALL_NAMESPACE}" contract-version="1" call-id="fixture-call"
            peer-jid="peer-lid@lid" chat-jid="contact@whatsapp.example.test"
            direction="unknown" kind="unknown" state="unknown"
            event-timestamp="2026-08-31T12:00:00Z" sequence="4"
            duration-seconds="42" outcome="connected" source="app_state" /></message>'''
        )
        event = call_event_from_xml(stanza)

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(
            (event.direction, event.kind, event.state), ("unknown", "unknown", "unknown")
        )
        self.assertIsNone(event.answered_at)
        self.assertIsNone(event.ended_at)
        self.assertEqual(event.event_timestamp.tzinfo, UTC)
        self.assertEqual(event.peer_jid, "peer-lid@lid")
        self.assertEqual(event.chat_jid, self.contact_jid)
        self.assertEqual(event.duration_seconds, 42)
        self.assertEqual(event.outcome, "connected")
        self.assertEqual(event.source, "app_state")

        invalid_namespace = ET.fromstring(
            "<message><call contract-version='1' call-id='x' peer-jid='peer@example.test' "
            "direction='incoming' kind='voice' state='offered' "
            "event-timestamp='2026-08-31T12:00:00Z' sequence='1' /></message>"
        )
        invalid_state = ET.fromstring(
            f"<message><call xmlns='{CALL_NAMESPACE}' contract-version='1' call-id='x' "
            "peer-jid='peer@example.test' direction='incoming' kind='voice' state='made-up' "
            "event-timestamp='2026-08-31T12:00:00Z' sequence='1' /></message>"
        )
        invalid_timestamp = ET.fromstring(
            f"<message><call xmlns='{CALL_NAMESPACE}' contract-version='1' call-id='x' "
            "peer-jid='peer@example.test' direction='incoming' kind='voice' state='offered' "
            "event-timestamp='2026-08-31T12:00:00' sequence='1' /></message>"
        )
        invalid_duration = ET.fromstring(
            f"<message><call xmlns='{CALL_NAMESPACE}' contract-version='1' call-id='x' "
            "peer-jid='peer@example.test' direction='incoming' kind='voice' state='ended' "
            "event-timestamp='2026-08-31T12:00:00Z' sequence='4' "
            "duration-seconds='-1' outcome='connected' source='app_state' /></message>"
        )
        invalid_outcome = ET.fromstring(
            f"<message><call xmlns='{CALL_NAMESPACE}' contract-version='1' call-id='x' "
            "peer-jid='peer@example.test' direction='incoming' kind='voice' state='ended' "
            "event-timestamp='2026-08-31T12:00:00Z' sequence='4' "
            "outcome='invented' source='app_state' /></message>"
        )
        self.assertIsNone(call_event_from_xml(invalid_namespace))
        self.assertIsNone(call_event_from_xml(invalid_state))
        self.assertIsNone(call_event_from_xml(invalid_timestamp))
        self.assertIsNone(call_event_from_xml(invalid_duration))
        self.assertIsNone(call_event_from_xml(invalid_outcome))

        failed = ET.fromstring(
            f"<message><call xmlns='{CALL_NAMESPACE}' contract-version='1' call-id='x' "
            "peer-jid='peer@example.test' direction='incoming' kind='voice' state='failed' "
            "event-timestamp='2026-08-31T12:00:00Z' sequence='1' /></message>"
        )
        parsed_failed = call_event_from_xml(failed)
        self.assertIsNotNone(parsed_failed)
        assert parsed_failed is not None
        self.assertEqual(parsed_failed.state, "failed")

    def test_explicit_route_drives_live_and_mam_without_parsing_the_body(self) -> None:
        stanza = ET.fromstring(
            f'''<message xmlns="jabber:client"><body>Unrelated human text</body>
            <call xmlns="{CALL_NAMESPACE}" contract-version="1" call-id="routed-call"
            peer-jid="12345@lid" chat-jid="{self.contact_jid}" direction="incoming"
            kind="voice" state="offered" event-timestamp="2026-08-31T12:00:00Z"
            sequence="1" /></message>'''
        )
        event = call_event_from_xml(stanza)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(routed_chat_jid(self.component_jid, event), self.contact_jid)
        # The same routing boundary is used for live stanzas and MAM results.
        self.assertEqual(routed_chat_jid(self.component_jid, event), self.contact_jid)

        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "fixture.sqlite3")
            store.upsert_chats(
                self.account_jid, [Chat(jid=self.contact_jid, name="Fixture contact")]
            )
            store.upsert_messages(
                self.account_jid,
                [
                    Message(
                        chat_jid=routed_chat_jid(self.component_jid, event),
                        sender_jid=self.component_jid,
                        body="Unrelated human text",
                        sent_at=event.event_timestamp,
                        message_id="routed-live",
                        call=event,
                    )
                ],
            )
            individual = store.load_chat_statistics(
                self.account_jid,
                self.contact_jid,
                7,
                now=datetime(2026, 8, 31, 23, tzinfo=UTC),
            )
        self.assertIsNotNone(individual.overview)
        assert individual.overview is not None
        self.assertEqual(individual.overview.calls.total, 1)

    def test_old_envelope_without_route_stays_unassociated_with_a_contact(self) -> None:
        stanza = ET.fromstring(
            f'''<message xmlns="jabber:client"><call xmlns="{CALL_NAMESPACE}"
            contract-version="1" call-id="legacy-call" peer-jid="12345@lid"
            direction="incoming" kind="voice" state="offered"
            event-timestamp="2026-08-31T12:00:00Z" sequence="1" /></message>'''
        )
        event = call_event_from_xml(stanza)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.chat_jid, "")
        self.assertEqual(routed_chat_jid(self.component_jid, event), self.component_jid)

    def test_legacy_text_notice_is_not_a_structured_call(self) -> None:
        legacy = ET.fromstring(
            "<message><body>Incoming call from Example at "
            "2026-08-31 12:00:00+00:00</body></message>"
        )
        self.assertIsNone(call_event_from_xml(legacy))

    def test_upsert_is_idempotent_by_account_call_and_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "fixture.sqlite3")
            offered = self._event("dedupe", 1, "offered", at=datetime(2026, 8, 31, 12, tzinfo=UTC))
            store.upsert_messages(
                self.account_jid,
                [
                    self._message(offered, message_id="live"),
                    self._message(offered, message_id="mam", chat_jid="other@example.test"),
                ],
            )
            loaded = store.load_recent_messages(self.account_jid, self.contact_jid)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].call, offered)
            with store._connect() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE account_jid = ? "
                    "AND call_id = ? AND call_sequence = 1",
                    (self.account_jid, "dedupe"),
                ).fetchone()[0]
            self.assertEqual(count, 1)

    def test_migration_adds_call_columns_and_unique_identity_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.sqlite3"
            MessageStore(path)
            with closing(sqlite3.connect(path)) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
                indexes = {row[1] for row in conn.execute("PRAGMA index_list(messages)")}
            self.assertTrue(
                {
                    "call_id",
                    "call_sequence",
                    "call_event_at",
                    "call_ended_at",
                    "call_duration_seconds",
                    "call_outcome",
                    "call_source",
                }
                <= columns
            )
            self.assertIn("idx_messages_call_identity", indexes)

    def test_migration_repairs_accepted_elsewhere_without_inventing_duration(self) -> None:
        at = datetime(2026, 9, 2, 19, 34, 23, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.sqlite3"
            store = MessageStore(path)
            event = self._event(
                "accepted-elsewhere",
                3,
                "ended",
                at=at,
                ended_at=at,
                terminal_reason="accepted_elsewhere",
            )
            store.upsert_messages(
                self.account_jid,
                [
                    Message(
                        chat_jid=self.contact_jid,
                        sender_jid=self.component_jid,
                        body="Call ended with Contacto de prueba at 2026-09-02 19:34:23+00:00",
                        sent_at=at,
                        message_id="accepted-elsewhere",
                        call=event,
                    )
                ],
            )
            repaired = MessageStore(path).load_recent_messages(
                self.account_jid, self.contact_jid
            )
        self.assertEqual(len(repaired), 1)
        self.assertIsNotNone(repaired[0].call)
        assert repaired[0].call is not None
        self.assertEqual(repaired[0].call.state, "accepted")
        self.assertIsNone(repaired[0].call.ended_at)
        self.assertIsNone(repaired[0].call.duration_seconds)
        self.assertEqual(
            repaired[0].body,
            "Call accepted with Contacto de prueba at 2026-09-02 19:34:23+00:00",
        )

    def test_migration_reorders_recovered_calls_by_event_time(self) -> None:
        event_at = datetime(2026, 6, 23, 20, 1, 2, tzinfo=UTC)
        arrival_at = datetime(2026, 9, 3, 2, 36, 27, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.sqlite3"
            store = MessageStore(path)
            event = self._event(
                "recovered-order",
                4,
                "ended",
                at=event_at,
                direction="incoming",
                source="app_state",
            )
            store.upsert_messages(
                self.account_jid,
                [
                    Message(
                        chat_jid=self.contact_jid,
                        sender_jid=self.component_jid,
                        body="Incoming voice call: missed with Contacto de prueba",
                        sent_at=arrival_at,
                        message_id="recovered-order",
                        call=event,
                    )
                ],
            )
            reopened = MessageStore(path)
            loaded = reopened.load_recent_messages(self.account_jid, self.contact_jid)
            chats = reopened.load_chats(self.account_jid)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].sent_at, event_at)
        self.assertEqual(chats[0].last_message_at, event_at)

    def test_statistics_merge_phases_without_counting_messages_or_invalid_durations(self) -> None:
        at = datetime(2026, 8, 31, 12, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "fixture.sqlite3")
            store.upsert_chats(
                self.account_jid, [Chat(jid=self.contact_jid, name="Fixture contact")]
            )
            events = [
                self._event("answered", 1, "offered", at=at),
                self._event("answered", 2, "accepted", at=at, answered_at=at),
                self._event(
                    "answered",
                    3,
                    "ended",
                    at=at,
                    answered_at=at,
                    ended_at=datetime(2026, 8, 31, 12, 2, tzinfo=UTC),
                ),
                self._event("missed", 3, "missed", at=at, kind="video"),
                self._event("rejected", 3, "rejected", at=at, direction="outgoing"),
                self._event("failed", 3, "ended", at=at, terminal_reason="failed"),
            ]
            store.upsert_messages(
                self.account_jid,
                [
                    self._message(event, message_id=f"fixture-{index}")
                    for index, event in enumerate(events)
                ],
            )
            store.upsert_messages(
                self.account_jid,
                [
                    Message(
                        chat_jid=self.contact_jid,
                        sender_jid=self.contact_jid,
                        body="Incoming call from legacy fallback",
                        sent_at=at,
                        message_id="legacy-text",
                    )
                ],
            )
            statistics = store.load_statistics(
                self.account_jid,
                7,
                now=datetime(2026, 8, 31, 23, tzinfo=UTC),
            )
            calls = statistics.calls
            self.assertEqual(statistics.total, 1)
            self.assertEqual(
                (calls.total, calls.answered, calls.missed, calls.rejected, calls.failed),
                (4, 1, 1, 1, 1),
            )
            self.assertEqual(
                (calls.incoming, calls.outgoing, calls.voice, calls.video), (3, 1, 3, 1)
            )
            self.assertEqual(
                (calls.duration_total_seconds, calls.duration_count, calls.median_duration_seconds),
                (120, 1, 120),
            )
            self.assertEqual(statistics.daily[-1].calls.total, 4)
            self.assertEqual(statistics.chats[0].calls.total, 4)

    def test_authoritative_record_replaces_ongoing_record_and_keeps_exact_duration(self) -> None:
        at = datetime(2026, 8, 31, 12, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "fixture.sqlite3")
            store.upsert_chats(
                self.account_jid, [Chat(jid=self.contact_jid, name="Fixture contact")]
            )
            ongoing = self._event(
                "recorded",
                4,
                "unknown",
                at=at,
                direction="outgoing",
                kind="video",
                outcome="ongoing",
                source="history_sync",
            )
            completed = self._event(
                "recorded",
                4,
                "ended",
                at=at,
                direction="outgoing",
                kind="video",
                duration_seconds=37,
                outcome="connected",
                source="app_state",
            )
            store.upsert_messages(
                self.account_jid, [self._message(ongoing, message_id="history-record")]
            )
            store.upsert_messages(
                self.account_jid, [self._message(completed, message_id="app-state-record")]
            )
            # A delayed initial sync must not roll a final real-time record back to ongoing.
            store.upsert_messages(
                self.account_jid, [self._message(ongoing, message_id="late-history-record")]
            )

            loaded = store.load_recent_messages(self.account_jid, self.contact_jid)
            self.assertEqual(len(loaded), 1)
            self.assertIsNotNone(loaded[0].call)
            assert loaded[0].call is not None
            self.assertEqual(loaded[0].call.duration_seconds, 37)
            self.assertEqual(loaded[0].call.outcome, "connected")
            self.assertEqual(loaded[0].call.source, "app_state")

            calls = store.load_statistics(
                self.account_jid,
                7,
                now=datetime(2026, 8, 31, 23, tzinfo=UTC),
            ).calls
            self.assertEqual(
                (calls.total, calls.answered, calls.outgoing, calls.video),
                (1, 1, 1, 1),
            )
            self.assertEqual((calls.duration_total_seconds, calls.duration_count), (37, 1))

    def test_authoritative_outcomes_keep_cancellation_failure_and_ongoing_distinct(self) -> None:
        at = datetime(2026, 8, 31, 12, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "fixture.sqlite3")
            store.upsert_chats(
                self.account_jid, [Chat(jid=self.contact_jid, name="Fixture contact")]
            )
            outcomes = (
                "cancelled",
                "unavailable",
                "invalid",
                "ongoing",
                "silenced_by_dnd",
                "silenced_unknown_caller",
            )
            store.upsert_messages(
                self.account_jid,
                [
                    self._message(
                        self._event(
                            f"outcome-{outcome}",
                            4,
                            "unknown" if outcome == "ongoing" else "ended",
                            at=at,
                            outcome=outcome,
                            source="history_sync",
                        ),
                        message_id=f"record-{outcome}",
                    )
                    for outcome in outcomes
                ],
            )
            calls = store.load_statistics(
                self.account_jid,
                7,
                now=datetime(2026, 8, 31, 23, tzinfo=UTC),
            ).calls

        self.assertEqual(calls.total, 6)
        self.assertEqual(
            (calls.cancelled, calls.unavailable, calls.failed, calls.ongoing, calls.missed),
            (1, 1, 1, 1, 2),
        )

    def test_component_chat_has_no_local_chat_participants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "fixture.sqlite3")
            store.upsert_chats(
                self.account_jid,
                [
                    Chat(jid=self.component_jid, name="Administration"),
                    Chat(jid=self.contact_jid, name="Fixture contact"),
                ],
            )
            store.upsert_messages(
                self.account_jid,
                [
                    Message(
                        chat_jid=self.component_jid,
                        sender_jid=self.component_jid,
                        body="Administrative text",
                        sent_at=datetime(2026, 8, 31, 12, tzinfo=UTC),
                        message_id="admin",
                    )
                ],
            )
            local = store.load_chat_statistics(
                self.account_jid,
                self.component_jid,
                7,
                now=datetime(2026, 8, 31, 23, tzinfo=UTC),
            )
            self.assertIsNone(local.overview)
            self.assertEqual(local.participants, ())
            self.assertEqual(local.hourly_activity, ())
            self.assertEqual(local.recurrent_phrases, ())


if __name__ == "__main__":
    unittest.main()
