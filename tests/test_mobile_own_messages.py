from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from xml.etree import ElementTree as ET

from slixmpp import JID
from slixmpp.stanza import Message as StanzaMessage
from slixmpp.stanza import Presence

from cliente_xmpp.config.settings import ConnectionSettings
from cliente_xmpp.models.chat import Message
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.xmpp.client import BridgeXmppClient
from cliente_xmpp.xmpp.events import MessageReceived

ROOM = "#fixture@whatsapp.example.test"
ACCOUNT = "owner@example.test"


class MobileOwnMessageTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.client = BridgeXmppClient(
            ConnectionSettings(jid=ACCOUNT), "fixture", self.events.append,
        )

    def presence(self, nick="MobileOwner", *, kind="available", code="110"):
        xml = ET.fromstring(f'''
            <presence xmlns="jabber:client" from="{ROOM}/{nick}">
              <x xmlns="http://jabber.org/protocol/muc#user"><status code="{code}"/></x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0" id="self-fixture"/>
            </presence>''')
        self.client._handle_group_membership_presence(ROOM, kind, xml)

    def stanza(self, *, room=ROOM, nick="MobileOwner", occupant="self-fixture", extra=""):
        return StanzaMessage(xml=ET.fromstring(f'''
            <message xmlns="jabber:client" from="{room}/{nick}" type="groupchat" id="native-1">
              <body>Fixture text</body>
              <occupant-id xmlns="urn:xmpp:occupant-id:0" id="{occupant}"/>
              {extra}
            </message>'''), stream=self.client)

    def test_phone_text_and_audio_use_actual_self_occupant_and_never_other_room(self):
        self.presence()
        for media in ("", '<x xmlns="jabber:x:oob"><url>https://example.test/fixture.ogg</url></x>'):
            with self.subTest(audio=bool(media)):
                self.client._on_groupchat_message(self.stanza(extra=media))
                event = self.events[-1]
                self.assertIsInstance(event, MessageReceived)
                self.assertTrue(event.message.outgoing)
                self.assertEqual(event.message.sender_jid, "Yo")
                self.assertEqual(event.message.sender_name, "")
                if media:
                    self.assertEqual(event.message.media_kind, "audio")
        other = self.stanza(room="#other@whatsapp.example.test")
        self.assertFalse(self.client._message_xml_is_outgoing(other.xml, is_group=True))

    def test_authoritative_foreign_jid_and_different_occupant_do_not_impersonate_self(self):
        self.presence()
        for stanza in (
            self.stanza(occupant="other-fixture"),
            self.stanza(extra='''<x xmlns="http://jabber.org/protocol/muc#user">
                <item jid="other@example.test"/></x>'''),
        ):
            with self.subTest(xml=bool(stanza.xml.find("{http://jabber.org/protocol/muc#user}x"))):
                self.assertFalse(self.client._message_xml_is_outgoing(stanza.xml, is_group=True))
        # A wrong occupant may even have the client's originally requested nickname.
        self.assertFalse(self.client._message_xml_is_outgoing(
            self.stanza(nick="owner", occupant="other-fixture").xml, is_group=True,
        ))

    def test_rename_leave_and_disconnect_discard_old_identity(self):
        self.presence(code="100")
        self.assertNotIn(ROOM, self.client._own_group_occupants)
        self.presence()
        self.presence(kind="unavailable")
        self.assertNotIn(ROOM, self.client._own_group_occupants)
        self.presence(nick="NewOwner")
        self.assertTrue(self.client._message_xml_is_outgoing(
            self.stanza(nick="NewOwner", occupant="").xml, is_group=True,
        ))
        self.assertFalse(self.client._message_xml_is_outgoing(
            self.stanza(occupant="").xml, is_group=True,
        ))
        self.client._disconnect_requested = True
        self.client._on_disconnected(None)
        self.assertFalse(self.client._own_group_occupants)

    def test_archive_own_jid_does_not_depend_on_current_nickname(self):
        self.presence()
        stanza = self.stanza(nick="OldOwner", occupant="", extra=f'''
            <x xmlns="http://jabber.org/protocol/muc#user"><item jid="{ACCOUNT}"/></x>''')
        self.assertTrue(self.client._message_xml_is_outgoing(stanza.xml, is_group=True))

    def test_membership_ping_uses_assigned_self_resource_not_requested_nickname(self):
        self.presence()
        self.client._group_chat_jids.add(ROOM)
        self.client.register_plugin("xep_0199")
        with (
            patch.object(self.client, "is_connected", return_value=True),
            patch.object(self.client["xep_0199"], "ping", new_callable=AsyncMock) as ping,
        ):
            asyncio.run(self.client._refresh_group_membership(ROOM, 0))
            self.assertEqual(ping.call_args.args[0], f"{ROOM}/MobileOwner")

    def test_individual_own_reflection_without_carbon_is_outgoing_not_incoming(self):
        for media in ("", '<x xmlns="jabber:x:oob"><url>https://example.test/fixture.ogg</url></x>'):
            with self.subTest(audio=bool(media)):
                self.events.clear()
                stanza = StanzaMessage(xml=ET.fromstring(f'''
                    <message xmlns="jabber:client" from="{ACCOUNT}/phone"
                        to="contact@whatsapp.example.test" type="chat" id="phone-1">
                      <body>Fixture text</body>{media}
                    </message>'''), stream=self.client)
                self.client._on_message(stanza)
                event = next(e for e in self.events if isinstance(e, MessageReceived))
                self.assertTrue(event.message.outgoing)
                self.assertEqual(event.message.chat_jid, "contact@whatsapp.example.test")
                if media:
                    self.assertEqual(event.message.media_kind, "audio")


class MobileOwnPresenceDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_muc_presence_learns_self_when_generic_roster_events_are_suppressed(self):
        events = []
        client = BridgeXmppClient(ConnectionSettings(jid=ACCOUNT), "fixture", events.append)
        client.register_plugin("xep_0045")
        muc = client["xep_0045"]
        muc.rooms[None][JID(ROOM)] = {}
        other = Presence(xml=ET.fromstring(f'''
            <presence xmlns="jabber:client" from="{ROOM}/Other" to="{ACCOUNT}/can">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item jid="other@example.test"/>
              </x>
            </presence>'''), stream=client)
        # The actual library suppresses generic presence for MUC roster entries.
        # Calling only our classification helper would miss this event-wiring bug.
        muc._handle_groupchat_presence(other)
        self.assertTrue(client.roster[ROOM].ignore_updates)
        own = Presence(xml=ET.fromstring(f'''
            <presence xmlns="jabber:client" from="{ROOM}/MobileOwner" to="{ACCOUNT}/can">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item jid="{ACCOUNT}"/><status code="110"/>
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0" id="self-fixture"/>
            </presence>'''), stream=client)
        client._handle_presence(own)
        muc._handle_groupchat_presence(own)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(
            client._own_group_occupants.get(ROOM), (f"{ROOM}/MobileOwner", "self-fixture"),
        )
        message = StanzaMessage(xml=ET.fromstring(f'''
            <message xmlns="jabber:client" from="{ROOM}/MobileOwner"
                     type="groupchat" id="native-fixture">
              <body>Fixture text</body>
              <occupant-id xmlns="urn:xmpp:occupant-id:0" id="self-fixture"/>
            </message>'''), stream=client)
        muc._handle_groupchat_message(message)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        received = [event for event in events if isinstance(event, MessageReceived)]
        self.assertEqual(len(received), 1)
        self.assertTrue(received[0].message.outgoing)
        self.assertEqual(received[0].message.sender_jid, "Yo")


class OwnMessageMergeTests(unittest.TestCase):
    def message(self, **kwargs):
        return Message(ROOM, f"{ROOM}/MobileOwner", "Fixture text", message_id="native-1",
                       chat_is_group=True, **kwargs)

    def test_exact_history_identity_promotes_author_without_losing_media_reply_or_star(self):
        target = self.message(
            starred=True, reply_quote="Fixture quote", media_local_path="fixture.ogg",
        )
        own = replace(target, outgoing=True, sender_jid="Yo", sender_name="", media_local_path="")
        MainWindow._merge_message_metadata(target, own)
        self.assertTrue(target.outgoing)
        self.assertEqual(target.sender_jid, "Yo")
        self.assertTrue(target.starred)
        self.assertEqual(target.reply_quote, "Fixture quote")
        self.assertEqual(target.media_local_path, "fixture.ogg")
        MainWindow._merge_message_metadata(target, replace(own, outgoing=False))
        self.assertTrue(target.outgoing)

    def test_body_time_match_cannot_promote_another_message_or_chat(self):
        for changes in ({"message_id": "native-2"}, {"chat_jid": "#other@example.test"}):
            target = self.message()
            MainWindow._merge_message_metadata(target, replace(target, outgoing=True, **changes))
            self.assertFalse(target.outgoing)

    def test_sqlite_own_author_survives_stale_live_copy_and_stays_account_chat_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "fixture.sqlite3")
            incoming = self.message(starred=True, media_local_path="fixture.ogg")
            own = replace(incoming, outgoing=True, sender_jid="Yo", sender_name="")
            store.upsert_messages(ACCOUNT, [incoming])
            store.upsert_messages(ACCOUNT, [own])
            store.upsert_messages(ACCOUNT, [incoming])
            store.upsert_messages("other@example.test", [incoming])
            store.upsert_messages(ACCOUNT, [replace(incoming, chat_jid="#other@example.test")])
            rows = store.load_recent_messages(ACCOUNT, ROOM)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0].outgoing)
            self.assertEqual(rows[0].sender_jid, "Yo")
            self.assertTrue(rows[0].starred)
            self.assertEqual(rows[0].media_local_path, "fixture.ogg")
            self.assertFalse(store.load_recent_messages("other@example.test", ROOM)[0].outgoing)
            self.assertFalse(store.load_recent_messages(ACCOUNT, "#other@example.test")[0].outgoing)

    def test_direct_info_reads_live_local_state_and_rejects_account_switch_without_network(self):
        message = Message("contact@example.test", "Yo", "Fixture", outgoing=True, message_id="id")
        updated = replace(message, delivery_state="read")
        window = SimpleNamespace(current_jid=ACCOUNT, _message_by_id=Mock(return_value=updated))
        self.assertEqual(
            MainWindow._local_direct_message_info(window, ACCOUNT, message), (updated, ""),
        )
        window.current_jid = "other@example.test"
        window._message_by_id.reset_mock()
        result, error = MainWindow._local_direct_message_info(window, ACCOUNT, message)
        self.assertIsNone(result)
        self.assertTrue(error)
        window._message_by_id.assert_not_called()
