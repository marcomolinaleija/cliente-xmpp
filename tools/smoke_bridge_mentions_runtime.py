from __future__ import annotations

import asyncio
from types import SimpleNamespace

from slidge.group.room import LegacyMUC
from slixmpp import JID
from slixmpp.xmlstream import ET


class _OrmSession:
    def __enter__(self) -> _OrmSession:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def add(self, _stored: object) -> None:
        return None


class _FakeRoom:
    def __init__(self) -> None:
        self.xmpp = SimpleNamespace(
            store=SimpleNamespace(session=lambda: _OrmSession())
        )
        self.stored = SimpleNamespace(
            participants=[
                SimpleNamespace(
                    nickname="wa-jessy",
                    jid="+521111111111@whatsapp.xmpp.marco-ml.com",
                ),
                SimpleNamespace(
                    nickname="wa-angel",
                    jid="+522222222222@whatsapp.xmpp.marco-ml.com",
                ),
            ]
        )

    async def _LegacyMUC__fill_participants(self) -> None:
        return None

    @staticmethod
    def participant_from_store(stored: SimpleNamespace) -> SimpleNamespace:
        return SimpleNamespace(contact=SimpleNamespace(jid=JID(stored.jid)))


async def main() -> None:
    room = _FakeRoom()
    body = "Hola Jessy Herrera y Ángel"
    message_xml = ET.fromstring(
        b"""
        <message>
          <reference xmlns="urn:xmpp:reference:0" type="mention"
                     uri="xmpp:+521111111111@whatsapp.xmpp.marco-ml.com"
                     begin="5" end="18" />
          <reference xmlns="urn:xmpp:reference:0" type="mention"
                     uri="xmpp:+522222222222@whatsapp.xmpp.marco-ml.com/device"
                     begin="21" end="26" />
        </message>
        """
    )

    mentions = await LegacyMUC.parse_mentions(room, body, message_xml)
    parsed = [
        (mention.start, mention.end, str(mention.contact.jid.bare))
        for mention in mentions
    ]
    assert parsed == [
        (5, 18, "+521111111111@whatsapp.xmpp.marco-ml.com"),
        (21, 26, "+522222222222@whatsapp.xmpp.marco-ml.com"),
    ]

    invalid_xml = ET.fromstring(
        b"""
        <message>
          <reference xmlns="urn:xmpp:reference:0" type="mention"
                     uri="xmpp:+521111111111@whatsapp.xmpp.marco-ml.com"
                     begin="99" end="100" />
        </message>
        """
    )
    fallback = await LegacyMUC.parse_mentions(room, "wa-jessy presente", invalid_xml)
    assert [(mention.start, mention.end) for mention in fallback] == [(0, 8)]
    assert await LegacyMUC.parse_mentions(room, None, message_xml) == ()

    print("Bridge mention smoke test passed: explicit references and fallback.")


if __name__ == "__main__":
    asyncio.run(main())
