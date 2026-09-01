from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from cliente_xmpp.models.chat import Message
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.xmpp.calls import CALL_NAMESPACE, call_event_from_xml, routed_chat_jid


def main() -> None:
    stanza = ET.fromstring(
        f'''<message xmlns="jabber:client"><body>Synthetic fallback notice</body>
        <call xmlns="{CALL_NAMESPACE}" contract-version="1" call-id="fixture-call"
        peer-jid="peer-lid@lid" chat-jid="contact@example.test"
        direction="incoming" kind="voice" state="accepted"
        event-timestamp="2026-08-31T12:00:00Z" answered-at="2026-08-31T12:00:00Z"
        ended-at="2026-08-31T12:02:00Z" sequence="3" /></message>'''
    )
    event = call_event_from_xml(stanza)
    if event is None:
        raise SystemExit("The documented v1 fixture was rejected")
    with tempfile.TemporaryDirectory() as directory:
        store = MessageStore(Path(directory) / "calls-fixture.sqlite3")
        store.upsert_messages(
            "owner@example.test",
            [
                Message(
                    chat_jid=routed_chat_jid("whatsapp.example.test", event),
                    sender_jid="component.example.test",
                    body="Synthetic fallback notice",
                    sent_at=event.event_timestamp,
                    message_id="fixture-message",
                    call=event,
                )
            ],
        )
        statistics = store.load_statistics(
            "owner@example.test",
            7,
            now=datetime(2026, 8, 31, 23, tzinfo=UTC),
        )
    if statistics.calls.total != 1 or statistics.calls.duration_total_seconds != 120:
        raise SystemExit("Synthetic persistence or aggregate result did not match")
    print("OK: parsed routed stanza, persisted one contact call, aggregate duration=120 seconds")


if __name__ == "__main__":
    main()
