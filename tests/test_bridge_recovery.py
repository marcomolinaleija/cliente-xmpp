from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_recovery import (
    attachment_source,
    patch_package,
    recovery_source,
    room_source,
    roster_contact_source,
    roster_group_source,
    roster_native_source,
    roster_session_source,
    session_source,
)

# ruff: noqa: E501 -- exact upstream fixtures for the guarded patcher

ROOT = Path(__file__).resolve().parents[1]
EXTENDED = ("\t\tif payload == nil {\n\t\t\tpayload = &waE2E.Message{ExtendedTextMessage: "
            "&waE2E.ExtendedTextMessage{Text: &message.Body}}\n\t\t}\n")
SESSION = (
    "func (s *Session) getMessagePayload(ctx context.Context, message Message) *waE2E.Message {\n"
    "\tvar payload *waE2E.Message\n" + EXTENDED + EXTENDED +
    "\tif message.Location.Latitude > 0 || message.Location.Longitude > 0 {\n"
    "\t\tif payload == nil {\n"
    "\t\t\tpayload = &waE2E.Message{LocationMessage: &waE2E.LocationMessage{}}\n\t\t}\n"
    "\t\tpayload.LocationMessage.DegreesLatitude = &message.Location.Latitude\n"
    "\t\tpayload.LocationMessage.DegreesLongitude = &message.Location.Longitude\n"
    "\t\tpayload.LocationMessage.AccuracyInMeters = ptrTo(uint32(message.Location.Accuracy))\n"
    "\t}\n}\n// GenerateMessageID\n"
    "func (s *Session) MarkChatRead(receipt Receipt) error {\n"
    "\treturn fetch(func() error {\n\t\treturn err\n\t})\n}\n"
)
for _name, _result in (("GetContacts", "contacts"), ("GetGroups", "groups")):
    SESSION += (f"func (s *Session) {_name}() {{\n"
                "\tif s.client == nil || s.client.Store.ID == nil {\n\t\treturn nil, err\n\t}\n"
                f"\treturn {_result}, nil\n}}\n")
SESSION += ("func (s *Session) SubscribeToPresences() error {\n"
            "\treturn s.subscribeToPresences(s.ctx, s.currentClient())\n}\n")
ROSTER_SESSION = '''class Session:
    async def sync(self):
        if True:
            wa_contacts = list(
                self.whatsapp.GetContacts(refresh=True)  # type:ignore
            )
    async def preferences(self):
        if True:
            for wa_contact in self.whatsapp.GetContacts(refresh=True):  # type:ignore[no-untyped-call]
                pass
'''
ROSTER_CONTACT = '''class Roster:
    async def fill(self):
        wa_contacts = list(
            self.session.whatsapp.GetContacts(  # type:ignore[no-untyped-call]
                refresh=config.ALWAYS_SYNC_ROSTER
            )
        )
        self.session.whatsapp.SubscribeToPresences()  # type:ignore[no-untyped-call]
'''
ROSTER_GROUP = '''class Bookmarks:
    async def fill(self):
        groups = self.session.whatsapp.GetGroups()  # type:ignore[no-untyped-call]
'''
ROOM = '''class Room:
    def get_archived_messages(self, msg_id):
        with self.xmpp.store.session() as orm:
            for stored in self.xmpp.store.mam.get_messages(
                orm, self.stored.id, ids=[str(msg_id)]
            ):
                yield HistoryMessage(stored.stanza)


def set_origin_id(msg, origin_id):
    pass
'''
ATTACHMENT = '''class Attachment:
    async def get_stored(self):
        if True:
            with self.xmpp.store.session() as orm:
                stored = orm.get()
                if stored is not None:
                    if not await self.__valid_url(stored.url):
                        stored.url = None  # type:ignore
                    return stored
'''


class BridgeRecoveryPatchTests(unittest.TestCase):
    def fixtures(self):
        return {
            "slidge_whatsapp/session.go": SESSION,
            "slidge_whatsapp/can_read_state_recovery.go":
                (ROOT / "tools/bridge_read_state_recovery.go").read_text(encoding="utf-8"),
            "slidge_whatsapp/session.py": ROSTER_SESSION,
            "slidge_whatsapp/contact.py": ROSTER_CONTACT,
            "slidge_whatsapp/group.py": ROSTER_GROUP,
            "slidge/group/room.py": ROOM,
            "slidge/core/mixins/attachment.py": ATTACHMENT,
        }

    def test_transforms_are_idempotent_and_reject_unknown_sources(self):
        transforms = (lambda s: roster_native_source(session_source(s)), recovery_source,
                      roster_session_source, roster_contact_source, roster_group_source,
                      room_source, attachment_source)
        for transform, source in zip(transforms, self.fixtures().values(), strict=True):
            with self.subTest(transform=transform.__name__):
                result = transform(source)
                self.assertNotEqual(result, source)
                self.assertEqual(transform(result), result)
                with self.assertRaises(ValueError):
                    transform("unsupported upstream source")

    def test_package_validates_all_anchors_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixtures = self.fixtures()
            for name, source in fixtures.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
            last = root / "slidge/core/mixins/attachment.py"
            last.write_text("unsupported upstream source", encoding="utf-8")
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with self.assertRaises(ValueError):
                patch_package(root)
            self.assertEqual(before, {p: p.read_bytes() for p in before})
            last.write_text(ATTACHMENT, encoding="utf-8")
            self.assertTrue(patch_package(root))
            self.assertFalse(patch_package(root))
