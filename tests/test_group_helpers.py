from __future__ import annotations

import base64
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from cliente_xmpp.models.chat import Message
from cliente_xmpp.models.names import display_label_from_jid, normalize_chat_name, unescape_jid_text
from cliente_xmpp.ui.main_window import MainWindow
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


class ChatListFocusTests(unittest.TestCase):
    def test_restores_returned_chat_selection_before_focus(self) -> None:
        calls: list[tuple[str, str]] = []

        class FakeChatList:
            def select_chat_by_jid(self, jid: str) -> None:
                calls.append(("select", jid))

            def focus(self) -> None:
                calls.append(("focus", ""))

        window = SimpleNamespace(chat_list=FakeChatList())

        MainWindow._restore_chat_list_focus(window, "marco@example.org")

        self.assertEqual(calls, [("select", "marco@example.org"), ("focus", "")])


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

    def test_detects_slidge_qr_image_url_when_state_is_still_unknown(self) -> None:
        client = SimpleNamespace(
            _last_whatsapp_status_by_component={},
            _is_probable_whatsapp_bridge_jid=BridgeXmppClient._is_probable_whatsapp_bridge_jid,
        )

        self.assertTrue(
            BridgeXmppClient._is_whatsapp_qr_image(
                client,
                "whatsapp.example.org",
                "http://example.org/slidge-attachments/tmp-race.png",
                "http://example.org/slidge-attachments/tmp-race.png",
                "image",
            )
        )

    def test_connected_component_image_is_not_assumed_to_be_qr(self) -> None:
        client = SimpleNamespace(
            _last_whatsapp_status_by_component={"whatsapp.example.org": "connected\n"},
            _is_probable_whatsapp_bridge_jid=BridgeXmppClient._is_probable_whatsapp_bridge_jid,
        )

        self.assertFalse(
            BridgeXmppClient._is_whatsapp_qr_image(
                client,
                "whatsapp.example.org",
                "http://example.org/slidge-attachments/avatar.png",
                "http://example.org/slidge-attachments/avatar.png",
                "image",
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

    def test_reads_avatar_metadata_from_pubsub_iq(self) -> None:
        iq = SimpleNamespace(
            xml=ET.fromstring(
                """
                <iq>
                  <pubsub xmlns="http://jabber.org/protocol/pubsub">
                    <items node="urn:xmpp:avatar:metadata">
                      <item id="current">
                        <metadata xmlns="urn:xmpp:avatar:metadata">
                          <info id="avatar-id" type="image/png" bytes="1234" />
                        </metadata>
                      </item>
                    </items>
                  </pubsub>
                </iq>
                """
            )
        )

        self.assertEqual(
            BridgeXmppClient._avatar_metadata_from_iq(iq),
            ("avatar-id", "image/png"),
        )

    def test_reads_avatar_data_from_pubsub_iq(self) -> None:
        encoded = base64.b64encode(b"avatar-bytes").decode("ascii")
        iq = SimpleNamespace(
            xml=ET.fromstring(
                f"""
                <iq>
                  <pubsub xmlns="http://jabber.org/protocol/pubsub">
                    <items node="urn:xmpp:avatar:data">
                      <item id="avatar-id">
                        <data xmlns="urn:xmpp:avatar:data">{encoded}</data>
                      </item>
                    </items>
                  </pubsub>
                </iq>
                """
            )
        )

        self.assertEqual(BridgeXmppClient._avatar_data_from_iq(iq), b"avatar-bytes")


class ContactStateTests(unittest.TestCase):
    def test_presence_subscription_is_sent_once_for_contact(self) -> None:
        sent: list[tuple[str, str]] = []
        client = SimpleNamespace(
            _presence_subscription_jids=set(),
            _jid_may_be_group_chat=BridgeXmppClient._jid_may_be_group_chat,
            _is_probable_whatsapp_bridge_jid=BridgeXmppClient._is_probable_whatsapp_bridge_jid,
            send_presence_subscription=lambda **kwargs: sent.append(
                (kwargs["pto"], kwargs["ptype"])
            ),
        )

        BridgeXmppClient.request_contact_presence_subscription(
            client,
            "+5218126462159@whatsapp.example.org",
        )
        BridgeXmppClient.request_contact_presence_subscription(
            client,
            "+5218126462159@whatsapp.example.org",
        )

        self.assertEqual(sent, [("+5218126462159@whatsapp.example.org", "subscribe")])

    def test_presence_subscription_ignores_groups_and_component(self) -> None:
        sent: list[tuple[str, str]] = []
        client = SimpleNamespace(
            _presence_subscription_jids=set(),
            _jid_may_be_group_chat=BridgeXmppClient._jid_may_be_group_chat,
            _is_probable_whatsapp_bridge_jid=BridgeXmppClient._is_probable_whatsapp_bridge_jid,
            send_presence_subscription=lambda **kwargs: sent.append(
                (kwargs["pto"], kwargs["ptype"])
            ),
        )

        BridgeXmppClient.request_contact_presence_subscription(
            client,
            "#1203630@groups.whatsapp.example.org",
        )
        BridgeXmppClient.request_contact_presence_subscription(
            client,
            "whatsapp.example.org",
        )

        self.assertEqual(sent, [])

    def test_reads_composing_chat_state(self) -> None:
        message = ET.fromstring(
            """
            <message from="+5218126462159@whatsapp.example.org">
              <composing xmlns="http://jabber.org/protocol/chatstates" />
            </message>
            """
        )

        self.assertEqual(BridgeXmppClient._chat_state_from_xml(message), ("composing", ""))

    def test_reads_audio_media_chat_state(self) -> None:
        message = ET.fromstring(
            """
            <message from="+5218126462159@whatsapp.example.org">
              <composing xmlns="http://jabber.org/protocol/chatstates" media="audio" />
            </message>
            """
        )

        self.assertEqual(BridgeXmppClient._chat_state_from_xml(message), ("composing", "audio"))

    def test_emits_chatstate_for_contact_jid(self) -> None:
        events: list[object] = []
        client = SimpleNamespace(
            _emit=events.append,
            _debug_whatsapp=lambda _message: None,
            _jid_may_be_group_chat=BridgeXmppClient._jid_may_be_group_chat,
            _is_probable_whatsapp_bridge_jid=BridgeXmppClient._is_probable_whatsapp_bridge_jid,
        )

        BridgeXmppClient._emit_chat_state_update(
            client,
            "+5218126462159@whatsapp.example.org",
            "composing",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].chat_jid, "+5218126462159@whatsapp.example.org")
        self.assertEqual(events[0].state, "composing")
        self.assertEqual(events[0].media, "")

    def test_ignores_chatstate_from_group_or_component(self) -> None:
        events: list[object] = []
        client = SimpleNamespace(
            _emit=events.append,
            _debug_whatsapp=lambda _message: None,
            _jid_may_be_group_chat=BridgeXmppClient._jid_may_be_group_chat,
            _is_probable_whatsapp_bridge_jid=BridgeXmppClient._is_probable_whatsapp_bridge_jid,
        )

        BridgeXmppClient._emit_chat_state_update(
            client,
            "#1203630@groups.whatsapp.example.org",
            "composing",
        )
        BridgeXmppClient._emit_chat_state_update(
            client,
            "whatsapp.example.org",
            "composing",
        )

        self.assertEqual(events, [])

    def test_ignores_non_chatstate_composing_node(self) -> None:
        message = ET.fromstring(
            """
            <message from="+5218126462159@whatsapp.example.org">
              <composing xmlns="urn:example:not-chatstates" />
            </message>
            """
        )

        self.assertEqual(BridgeXmppClient._chat_state_from_xml(message), ("", ""))

    def test_reads_idle_since_from_presence(self) -> None:
        presence = ET.fromstring(
            """
            <presence from="+5218126462159@whatsapp.example.org">
              <idle xmlns="urn:xmpp:idle:1" since="2026-07-10T15:41:00Z" />
            </presence>
            """
        )

        last_seen = BridgeXmppClient._idle_datetime_from_xml(presence)

        self.assertIsNotNone(last_seen)
        self.assertEqual(last_seen.isoformat(), "2026-07-10T15:41:00+00:00")


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
    def test_retracted_message_id_from_xml(self) -> None:
        message = ET.fromstring(
            """
            <message xmlns="jabber:client" from="+1@whatsapp.example.org" type="chat">
              <retract xmlns="urn:xmpp:message-retract:1" id="original-id" />
            </message>
            """
        )

        self.assertEqual(
            BridgeXmppClient._retracted_message_id_from_xml(message),
            "original-id",
        )

    def test_forwarded_retraction_marks_message_deleted(self) -> None:
        client = SimpleNamespace(boundjid=SimpleNamespace(bare="angel@example.org"))
        client._group_chat_jids = set()
        client._forwarded_delay_from_xml = BridgeXmppClient._forwarded_delay_from_xml
        client._retracted_message = BridgeXmppClient._retracted_message
        client._retracted_message_id_from_xml = BridgeXmppClient._retracted_message_id_from_xml
        client._bare_jid = BridgeXmppClient._bare_jid
        client._message_xml_is_outgoing = lambda message, is_group=False: (
            BridgeXmppClient._message_xml_is_outgoing(client, message, is_group)
        )
        client._xml_message_addresses_groupchat = (
            BridgeXmppClient._xml_message_addresses_groupchat
        )
        result = ET.fromstring(
            """
            <result xmlns="urn:xmpp:mam:2">
              <forwarded xmlns="urn:xmpp:forward:0">
                <delay xmlns="urn:xmpp:delay" stamp="2026-07-10T12:00:00+00:00" />
                <message xmlns="jabber:client"
                         from="+1@whatsapp.example.org"
                         to="angel@example.org"
                         type="chat">
                  <retract xmlns="urn:xmpp:message-retract:1" id="original-id" />
                </message>
              </forwarded>
            </result>
            """
        )
        message = result.find(
            "{urn:xmpp:forward:0}forwarded/{jabber:client}message"
        )
        self.assertIsNotNone(message)

        retraction = BridgeXmppClient._message_retraction_from_xml(
            client,
            "+1@whatsapp.example.org",
            message,
            result,
        )

        self.assertIsNotNone(retraction)
        assert retraction is not None
        self.assertTrue(retraction.retracted)
        self.assertEqual(retraction.message_id, "original-id")
        self.assertEqual(retraction.chat_jid, "+1@whatsapp.example.org")
        self.assertFalse(retraction.outgoing)

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

    def test_group_sender_does_not_replace_custom_contact_name(self) -> None:
        sender_jid = "+5214495380505@whatsapp.example.org"
        window = SimpleNamespace(chat_names_by_jid={sender_jid: "Burra"})
        message = Message(
            chat_jid="#room@whatsapp.example.org",
            sender_jid=sender_jid,
            sender_name="Jessy Herrera",
            body="mensaje",
            chat_is_group=True,
        )

        MainWindow._remember_message_sender(window, message)

        self.assertEqual(window.chat_names_by_jid[sender_jid], "Burra")

    def test_group_sender_is_remembered_when_contact_is_unknown(self) -> None:
        sender_jid = "+5214495380505@whatsapp.example.org"
        window = SimpleNamespace(chat_names_by_jid={})
        message = Message(
            chat_jid="#room@whatsapp.example.org",
            sender_jid=sender_jid,
            sender_name="Jessy Herrera",
            body="mensaje",
            chat_is_group=True,
        )

        MainWindow._remember_message_sender(window, message)

        self.assertEqual(window.chat_names_by_jid[sender_jid], "Jessy Herrera")

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

    def test_group_self_echo_is_distinguished_from_bot_messages(self) -> None:
        outgoing = Message(
            chat_jid="#room@example.org",
            sender_jid="me",
            sender_name="Tú",
            body="hola",
            sent_at=datetime.now().astimezone(),
            outgoing=True,
            chat_is_group=True,
        )
        echo = Message(
            chat_jid="#room@example.org",
            sender_jid="#room@example.org/Ángel Alcantar",
            sender_name="Ángel Alcantar",
            body="hola",
            sent_at=outgoing.sent_at + timedelta(seconds=1),
            message_id="echo-1",
            chat_is_group=True,
        )
        bot = Message(
            chat_jid="#room@example.org",
            sender_jid="Yo",
            sender_name="Ángel Alcantar",
            body="hola",
            sent_at=outgoing.sent_at + timedelta(seconds=1),
            message_id="bot-1",
            outgoing=True,
            chat_is_group=True,
        )

        self.assertTrue(MainWindow._messages_are_group_self_echo(outgoing, echo))
        self.assertFalse(MainWindow._messages_are_group_self_echo(outgoing, bot))

    def test_repeated_fast_local_messages_are_not_merged_by_content(self) -> None:
        first = Message(
            chat_jid="+5218126462159@whatsapp.example.org",
            sender_jid="me",
            body="a",
            sent_at=datetime.now().astimezone(),
            outgoing=True,
            message_id="cliente-xmpp-first",
            delivery_state="pending",
        )
        second = Message(
            chat_jid="+5218126462159@whatsapp.example.org",
            sender_jid="me",
            body="a",
            sent_at=first.sent_at + timedelta(milliseconds=50),
            outgoing=True,
            message_id="cliente-xmpp-second",
            delivery_state="pending",
        )
        class MergeHarness:
            _matching_group_self_echo_index = staticmethod(
                MainWindow._matching_group_self_echo_index
            )
            _message_timestamp = staticmethod(MainWindow._message_timestamp)
            _message_merge_key = staticmethod(MainWindow._message_merge_key)
            _matching_content_message_index = staticmethod(
                MainWindow._matching_content_message_index
            )
            _message_content_key = staticmethod(MainWindow._message_content_key)
            _merge_message_metadata = staticmethod(MainWindow._merge_message_metadata)

            def __init__(self) -> None:
                self.messages_by_chat: dict[str, list[Message]] = {}

        window = MergeHarness()

        MainWindow._merge_messages(window, first.chat_jid, [first, second])

        messages = window.messages_by_chat[first.chat_jid]
        self.assertEqual([message.message_id for message in messages], [first.message_id, second.message_id])


if __name__ == "__main__":
    unittest.main()
