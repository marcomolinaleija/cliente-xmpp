from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from slidge.db.meta import Base
from slidge.db.models import Contact, Participant, Room
from slidge.group.room import LegacyMUC
from slixmpp import JID
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOM_LEGACY_ID = "120363000000000001@g.us"
CONTACT_LEGACY_ID = "15550000001@s.whatsapp.net"
OCCUPANT_ID = "100000000000001@lid"


async def exercise_anonymous_participant_upgrade() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    orm_factory = sessionmaker(engine)

    with orm_factory(expire_on_commit=False) as orm:
        room = Room(
            user_account_id=1,
            jid=JID("#120363000000000001@component.example.org"),
            legacy_id=ROOM_LEGACY_ID,
            updated=True,
        )
        contact = Contact(
            user_account_id=1,
            jid=JID("15550000001@component.example.org"),
            legacy_id=CONTACT_LEGACY_ID,
            updated=True,
        )
        orm.add_all((room, contact))
        orm.flush()
        orm.add(
            Participant(
                room=room,
                nickname="participant",
                nickname_no_illegal="participant",
                occupant_id=OCCUPANT_ID,
                resource="participant",
            )
        )
        orm.commit()
        room_id = room.id
        contact_id = contact.id

    with orm_factory() as orm:
        detached_room = orm.get(Room, room_id)
        detached_contact = orm.get(Contact, contact_id)
        assert detached_room is not None
        assert detached_contact is not None

    ready = asyncio.get_running_loop().create_future()
    ready.set_result(True)
    fake_muc = SimpleNamespace(
        session=SimpleNamespace(contacts=SimpleNamespace(ready=ready)),
        xmpp=SimpleNamespace(store=SimpleNamespace(session=orm_factory)),
        stored=detached_room,
        log=logging.getLogger("detached-room-smoke"),
        participant_from_store=lambda *, stored, contact: SimpleNamespace(
            stored=stored, contact=contact
        ),
    )
    fake_contact = SimpleNamespace(stored=detached_contact)

    participant = await LegacyMUC.get_participant_by_contact(
        fake_muc,
        fake_contact,
        occupant_id=OCCUPANT_ID,
    )

    assert participant is not None
    assert fake_muc.stored.id == room_id
    assert participant.stored.contact_id == contact_id


asyncio.run(exercise_anonymous_participant_upgrade())
print("Detached-room bridge runtime smoke: ok")
