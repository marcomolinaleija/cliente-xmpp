from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_read_markers import (
    marker_source,
    sqlite_source,
)
from tools.patch_slidge_read_markers import (
    patch_package as patch_read_markers,
)
from tools.patch_slidge_recovery import (
    archive_xml_source,
    attachment_source,
    muc_error_source,
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
MUC_ERROR = '''from ..util import DispatcherMixin, exceptions_to_xmpp_errors
class MucMiscMixin:
    async def __on_group_chat_error(self, msg):
        try:
            muc = await self.get_muc_from_stanza(msg)
        except XMPPError as e:
            log.debug("Not removing resource", exc_info=e)
            return
        muc.remove_user_resource(msg.get_from().resource)

    @exceptions_to_xmpp_errors
    async def on_ibr_remove(self, iq):
        pass
'''
ARCHIVE_XML = '''import uuid
class HistoryMessage:
    def __init__(self, stanza):
        if isinstance(stanza, str):
            from_db = True
            stanza = Message(xml=ET.fromstring(stanza))
        else:
            from_db = False
'''


class BridgeRecoveryPatchTests(unittest.TestCase):
    def test_muc_xml_image_is_an_offline_python_only_overlay_with_baseline_checks(self):
        source = (ROOT / "tools/Dockerfile.bridge-muc-xml").read_text(encoding="utf-8")
        self.assertIn("ARG BRIDGE_IMAGE=cliente-xmpp-bridge:r4-flat-base-20261022", source)
        self.assertIn('check(smoke, "--muc-errors-only", "--expect-muc-failure")', source)
        self.assertIn('check(smoke, "--archive-xml-only", "--expect-xml-failure")', source)
        self.assertIn("for _ in range(2):", source)
        self.assertEqual(source.count("RUN --network=none"), 1)
        self.assertIn("--mount=type=bind", source)
        self.assertNotIn("\nCOPY ", source)
        self.assertIn("user=user.pw_uid, group=user.pw_gid", source)
        self.assertNotIn("go build", source)
        self.assertNotIn("pip install", source)
        self.assertIn("RUN --network=none", source)
        self.assertIn("smoke_bridge_read_markers_runtime.py", source)
        self.assertIn("smoke_bridge_linking_runtime.py", source)
        self.assertIn("smoke_bridge_muc_history_runtime.py", source)

    def test_archive_xml_patch_is_idempotent_and_does_not_hide_parse_errors(self):
        result = archive_xml_source(ARCHIVE_XML)
        self.assertEqual(archive_xml_source(result), result)
        self.assertIn('ET.fromstring(_CAN_INVALID_XML.sub("\\ufffd", stanza))', result)
        self.assertNotIn("except", result)
        for unknown in ("", ARCHIVE_XML + ARCHIVE_XML,
                        ARCHIVE_XML.replace("from_db = False", "from_db = None")):
            with self.subTest(source=unknown[:30]), self.assertRaises(ValueError):
                archive_xml_source(unknown)

    def test_muc_error_patch_catches_only_existing_flow_control_and_keeps_other_handlers(self):
        result = muc_error_source(MUC_ERROR)
        self.assertEqual(muc_error_source(result), result)
        self.assertIn("except (XMPPError, Ignore) as e:", result)
        self.assertNotIn("except BaseException", result)
        self.assertEqual(result[result.index("    @exceptions_to_xmpp_errors"):],
                         MUC_ERROR[MUC_ERROR.index("    @exceptions_to_xmpp_errors"):])
        for unknown in ("", MUC_ERROR + MUC_ERROR,
                        MUC_ERROR.replace("except XMPPError as e:", "except Exception as e:")):
            with self.subTest(source=unknown[:30]), self.assertRaises(ValueError):
                muc_error_source(unknown)

    def test_roster_deadline_upgrades_installed_r2_without_changing_other_timeouts(self):
        expected = roster_native_source(session_source(SESSION))
        installed = expected.replace("bounded roster snapshot v2", "bounded roster snapshot v1")
        installed = installed.replace("120*time.Second", "30*time.Second")
        self.assertEqual(installed.count("30*time.Second"), 3)
        self.assertEqual(roster_native_source(installed), expected)
        self.assertEqual(roster_native_source(expected), expected)
        with self.assertRaises(ValueError):
            roster_native_source(installed.replace("30*time.Second", "99*time.Second", 1))

    def test_contact_marker_patch_is_local_idempotent_and_rejects_unknown_sources(self):
        source = '''class MarkerMixin:
    async def __to_mark(self, recipient, msg_id):
        hook = getattr(recipient, 'can_unread_up_to', None)
        if hook is not None:
            return await hook(msg_id)
        if self.xmpp.MARK_ALL_MESSAGES:
            return recipient.pop_unread_xmpp_ids_up_to(msg_id)
        else:
            return [msg_id]
'''
        result = marker_source(source)
        self.assertEqual(marker_source(result), result)
        self.assertIn("return await hook(msg_id)", result)
        self.assertIn("return [msg_id]", result)
        for unknown in ("", source + source):
            with self.subTest(unknown=bool(unknown)), self.assertRaises(ValueError):
                marker_source(unknown)

    def test_sqlite_reset_patch_is_local_idempotent_and_rejects_unknown_sources(self):
        source = '''import sqlalchemy as sa
def get_engine(path, pool_size=5):
    engine = sa.create_engine(path, pool_size=pool_size)
    return engine
'''
        result = sqlite_source(source)
        self.assertEqual(sqlite_source(result), result)
        self.assertIn('if engine.dialect.name == "sqlite":', result)
        self.assertIn("dbapi_connection.rollback()", result)
        with self.assertRaises(ValueError):
            sqlite_source("unsupported engine source")

    def test_read_marker_package_validates_both_sources_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "slidge/core/dispatcher/message/marker.py"
            meta = root / "slidge/db/meta.py"
            marker.parent.mkdir(parents=True)
            meta.parent.mkdir(parents=True)
            original = ("class Marker:\n    async def pop(self, recipient, msg_id):\n"
                        "        if True:\n"
                        "            return recipient.pop_unread_xmpp_ids_up_to(msg_id)\n")
            marker.write_text(original, encoding="utf-8")
            meta.write_text("unsupported engine source", encoding="utf-8")
            with self.assertRaises(ValueError):
                patch_read_markers(root)
            self.assertEqual(marker.read_text(encoding="utf-8"), original)
            meta.write_text("def get_engine(path, pool_size=5):\n"
                            "    engine = sa.create_engine(path, pool_size=pool_size)\n"
                            "    return engine\n", encoding="utf-8")
            self.assertTrue(patch_read_markers(root))
            self.assertFalse(patch_read_markers(root))

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
            "slidge/core/dispatcher/muc/misc.py": MUC_ERROR,
            "slidge/util/archive_msg.py": ARCHIVE_XML,
        }

    def test_transforms_are_idempotent_and_reject_unknown_sources(self):
        transforms = (lambda s: roster_native_source(session_source(s)), recovery_source,
                      roster_session_source, roster_contact_source, roster_group_source,
                      room_source, attachment_source, muc_error_source, archive_xml_source)
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
            last = root / "slidge/util/archive_msg.py"
            last.write_text("unsupported upstream source", encoding="utf-8")
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with self.assertRaises(ValueError):
                patch_package(root)
            self.assertEqual(before, {p: p.read_bytes() for p in before})
            last.write_text(ARCHIVE_XML, encoding="utf-8")
            self.assertTrue(patch_package(root))
            self.assertFalse(patch_package(root))
