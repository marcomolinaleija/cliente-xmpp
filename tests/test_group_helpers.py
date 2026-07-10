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


class WhatsAppPairingCodeTests(unittest.TestCase):
    def test_extracts_code_after_label_instead_of_whatsapp_word(self) -> None:
        text = (
            "Please open the official WhatsApp client and input the following "
            "code: 1A2B-3C4D"
        )

        self.assertEqual(BridgeXmppClient._pairing_code_from_text(text), "1A2B-3C4D")

    def test_extracts_qr_from_bob_image_data(self) -> None:
        message = ET.fromstring(
            """
            <message from="whatsapp.example.org" type="chat">
              <body>Scan this QR code</body>
              <data xmlns="urn:xmpp:bob" type="image/png">iVBORw0KGgo=</data>
            </message>
            """
        )

        self.assertEqual(
            BridgeXmppClient._whatsapp_qr_image_data_from_xml(
                "whatsapp.example.org",
                "Scan this QR code",
                message,
            ),
            (b"\x89PNG\r\n\x1a\n", "image/png", "qr-whatsapp.png"),
        )

    def test_extracts_qr_from_data_uri(self) -> None:
        message = ET.fromstring(
            """
            <message from="whatsapp.example.org" type="chat">
              <body>QR scan needed</body>
              <html>
                <img src="data:image/png;base64,iVBORw0KGgo=" />
              </html>
            </message>
            """
        )

        self.assertEqual(
            BridgeXmppClient._whatsapp_qr_image_data_from_xml(
                "whatsapp.example.org",
                "QR scan needed",
                message,
            ),
            (b"\x89PNG\r\n\x1a\n", "image/png", "qr-whatsapp.png"),
        )

    def test_detects_slidge_qr_image_url_after_relogin(self) -> None:
        message = ET.fromstring(
            """
            <message xmlns="jabber:client"
                     xmlns:oob="jabber:x:oob"
                     xmlns:file="urn:xmpp:file:metadata:0"
                     from="whatsapp.example.org"
                     type="chat">
              <body>http://example.org/slidge-attachments/tmp.png</body>
              <file:file>
                <file:media-type>image/png</file:media-type>
                <file:name>tmp.png</file:name>
              </file:file>
              <oob:x>
                <oob:url>http://example.org/slidge-attachments/tmp.png</oob:url>
              </oob:x>
            </message>
            """
        )
        client = SimpleNamespace(
            _last_whatsapp_status_by_component={"whatsapp.example.org": "needs_relogin\n"},
            _is_probable_whatsapp_bridge_jid=BridgeXmppClient._is_probable_whatsapp_bridge_jid,
        )
        media_url, media_kind, _, _, _, _ = BridgeXmppClient._media_from_xml(message)

        self.assertEqual(media_url, "http://example.org/slidge-attachments/tmp.png")
        self.assertEqual(media_kind, "image")
        self.assertTrue(
            BridgeXmppClient._is_whatsapp_qr_image(
                client,
                "whatsapp.example.org",
                media_url,
                media_url,
                media_kind,
            )
        )

    def test_ignores_slidge_thumbhash_as_embedded_qr_image(self) -> None:
        message = ET.fromstring(
            """
            <message xmlns="jabber:client"
                     xmlns:oob="jabber:x:oob"
                     xmlns:file="urn:xmpp:file:metadata:0"
                     xmlns:thumb="urn:xmpp:thumbs:1"
                     from="whatsapp.example.org"
                     type="chat">
              <body>http://example.org/slidge-attachments/tmp.png</body>
              <file:file>
                <file:media-type>image/png</file:media-type>
                <file:name>tmp.png</file:name>
                <thumb:thumbnail media-type="image/thumbhash"
                                 uri="data:image/thumbhash;base64,JggKBwD3xw==" />
              </file:file>
              <oob:x>
                <oob:url>http://example.org/slidge-attachments/tmp.png</oob:url>
              </oob:x>
            </message>
            """
        )

        self.assertIsNone(
            BridgeXmppClient._whatsapp_qr_image_data_from_xml(
                "whatsapp.example.org",
                "http://example.org/slidge-attachments/tmp.png",
                message,
            )
        )
        media_url, media_kind, _, _, _, _ = BridgeXmppClient._media_from_xml(message)
        self.assertEqual(media_url, "http://example.org/slidge-attachments/tmp.png")
        self.assertEqual(media_kind, "image")

    def test_qr_timeout_presence_needs_relogin(self) -> None:
        self.assertEqual(
            BridgeXmppClient._whatsapp_state_hint(
                "You are not connected to this gateway! "
                "You did not flash the QR code in time. Use re-login when you are ready."
            ),
            "needs_relogin",
        )

    def test_component_status_message_is_admin_message(self) -> None:
        self.assertTrue(
            BridgeXmppClient._is_whatsapp_component_admin_message(
                "whatsapp.example.org",
                "Connected as +5218126462159",
            )
        )

    def test_contact_message_is_not_component_admin_message(self) -> None:
        self.assertFalse(
            BridgeXmppClient._is_whatsapp_component_admin_message(
                "+5218126462159@whatsapp.example.org",
                "Connected as +5218126462159",
            )
        )

    def test_reads_displayed_marker_id_from_xml(self) -> None:
        message = SimpleNamespace(
            xml=ET.fromstring(
                """
                <message from="+5218126462159@whatsapp.example.org">
                  <displayed xmlns="urn:xmpp:chat-markers:0" id="wa-message-id" />
                </message>
                """
            )
        )

        self.assertEqual(BridgeXmppClient._delivery_marker_id(message), "wa-message-id")


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
