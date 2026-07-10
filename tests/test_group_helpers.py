from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from cliente_xmpp.models.chat import Message
from cliente_xmpp.models.names import display_label_from_jid, normalize_chat_name, unescape_jid_text
from cliente_xmpp.xmpp.client import BridgeXmppClient


class GroupNameTests(unittest.TestCase):
    def test_unescapes_xep_0106_sequences(self) -> None:
        self.assertEqual(unescape_jid_text("familia\\20can\\2famigos"), "familia can/amigos")

    def test_uses_resource_as_group_sender_label(self) -> None:
        self.assertEqual(
            display_label_from_jid("familia@groups.example.org/Juan\\20Perez"),
            "Juan Perez",
        )

    def test_normalizes_chat_name_from_jid_when_name_is_missing(self) -> None:
        self.assertEqual(
            normalize_chat_name("mi\\20grupo@groups.example.org"),
            "mi grupo",
        )


class BookmarkNotificationTests(unittest.TestCase):
    def test_detects_muted_group_from_current_notification_namespace(self) -> None:
        conference = ET.fromstring(
            """
            <conference xmlns="urn:xmpp:bookmarks:1" name="Grupo">
              <extensions>
                <notify xmlns="urn:xmpp:notification-settings:1">
                  <never />
                </notify>
              </extensions>
            </conference>
            """
        )

        self.assertEqual(BridgeXmppClient._bookmark_notification_settings(conference), (True, True))

    def test_absent_notification_settings_are_unknown(self) -> None:
        conference = ET.fromstring('<conference xmlns="urn:xmpp:bookmarks:1" name="Grupo" />')

        self.assertEqual(
            BridgeXmppClient._bookmark_notification_settings(conference),
            (False, False),
        )

    def test_reads_legacy_storage_bookmarks(self) -> None:
        xml = ET.fromstring(
            """
            <iq>
              <query xmlns="jabber:iq:private">
                <storage xmlns="storage:bookmarks">
                  <conference jid="#120363@test.whatsapp.example"
                              name="Familia" autojoin="true">
                    <nick>angel</nick>
                  </conference>
                </storage>
              </query>
            </iq>
            """
        )
        chats = BridgeXmppClient._group_chats_from_legacy_bookmark_xml(xml)

        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0].jid, "#120363@test.whatsapp.example")
        self.assertEqual(chats[0].name, "Familia")
        self.assertTrue(chats[0].is_group)


class GroupArchiveTests(unittest.TestCase):
    def test_hash_prefixed_jid_is_whatsapp_group(self) -> None:
        self.assertTrue(
            BridgeXmppClient._jid_is_hash_group_chat(
                "#5214492757727-1485039809@whatsapp.example.org"
            )
        )

    def test_hash_prefixed_whatsapp_group_uses_room_archive(self) -> None:
        client = SimpleNamespace(_group_chat_jids=set())

        self.assertTrue(
            BridgeXmppClient._uses_room_archive(
                client,
                "#120363040866530452@whatsapp.example.org",
            )
        )

    def test_known_group_uses_room_archive(self) -> None:
        client = SimpleNamespace(_group_chat_jids={"room@example.org"})

        self.assertTrue(BridgeXmppClient._uses_room_archive(client, "room@example.org"))

    def test_hash_prefixed_forwarded_message_is_groupchat(self) -> None:
        message = ET.fromstring(
            """
            <message from="angel@example.org"
                     to="#120363040866530452@whatsapp.example.org"
                     type="chat" />
            """
        )

        self.assertTrue(BridgeXmppClient._xml_message_addresses_groupchat(message))

    def test_mam_group_result_uses_room_jid(self) -> None:
        client = SimpleNamespace(
            boundjid=SimpleNamespace(bare="angel@example.org"),
            _stanza_is_groupchat=lambda _stanza: True,
        )
        result = {
            "mam_result": {
                "forwarded": {
                    "stanza": {
                        "from": SimpleNamespace(bare="#room@example.org"),
                        "to": SimpleNamespace(bare="angel@example.org"),
                    }
                }
            }
        }

        self.assertEqual(
            BridgeXmppClient._chat_jid_from_mam_result(client, result),
            "#room@example.org",
        )


class GroupMessageParsingTests(unittest.TestCase):
    def test_group_sender_prefers_muc_user_item_jid(self) -> None:
        message = ET.fromstring(
            """
            <message xmlns="jabber:client"
                     type="groupchat"
                     from="#room@whatsapp.example.org/Burra">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item jid="+5214495380505@whatsapp.example.org" />
              </x>
            </message>
            """
        )

        self.assertEqual(
            BridgeXmppClient._sender_jid_from_message_xml(message, is_group=True),
            "+5214495380505@whatsapp.example.org",
        )

    def test_group_sender_matches_local_nick_case_and_accents_insensitively(self) -> None:
        client = SimpleNamespace(
            boundjid=SimpleNamespace(bare="angel@example.org"),
            _muc_nick=lambda: "Angel Alcantar",
        )

        self.assertTrue(
            BridgeXmppClient._group_sender_matches_local(
                client,
                "",
                "ÁNGEL ALCANTAR",
            )
        )

    def test_group_history_before_session_does_not_notify(self) -> None:
        client = SimpleNamespace(
            _session_started_at=datetime.now().astimezone(),
        )
        message = Message(
            chat_jid="#room@example.org",
            sender_jid="participant@example.org",
            body="histórico",
            sent_at=datetime.now().astimezone() - timedelta(minutes=1),
            chat_is_group=True,
        )

        self.assertTrue(BridgeXmppClient._message_predates_session(client, message))

    def test_reply_parts_from_quoted_body(self) -> None:
        self.assertEqual(
            BridgeXmppClient._reply_parts_from_quoted_body("> cita original\n\nrespuesta"),
            ("respuesta", "cita original"),
        )


if __name__ == "__main__":
    unittest.main()
