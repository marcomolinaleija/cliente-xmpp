from __future__ import annotations

import asyncio
import base64
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from cliente_xmpp.config.settings import ConnectionSettings
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.models.mentions import GroupParticipant
from cliente_xmpp.models.names import display_label_from_jid, normalize_chat_name, unescape_jid_text
from cliente_xmpp.ui.conversation_panel import ConversationPanel
from cliente_xmpp.ui.main_window import APP_WINDOW_TITLE, MainWindow
from cliente_xmpp.xmpp.client import BridgeXmppClient, XmppService
from cliente_xmpp.xmpp.events import (
    ChatDisplayedSynced,
    GroupParticipantsLoaded,
    GroupParticipantUpdated,
    WhatsAppBridgeStatus,
    WhatsAppQrImageDataReceived,
    XmppConnected,
)


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

    def test_keeps_known_group_title_when_discovery_only_has_its_jid(self) -> None:
        jid = "#5214492757727-1485039809@whatsapp.xmpp.rayoscompany.com"
        known_group = Chat(jid=jid, name="La familia de la Burra", is_group=True)
        incomplete_discovery = Chat(jid=jid, name=jid, is_group=True)

        merged = MainWindow._merge_chat_lists([known_group], [incomplete_discovery])

        self.assertEqual(merged[0].name, "La familia de la Burra")

    def test_uses_new_group_title_when_discovery_has_one(self) -> None:
        jid = "#120363418240465691@whatsapp.xmpp.rayoscompany.com"
        incomplete_group = Chat(jid=jid, name=jid, is_group=True)
        discovered_group = Chat(jid=jid, name="Cielo lluvioso", is_group=True)

        merged = MainWindow._merge_chat_lists([incomplete_group], [discovered_group])

        self.assertEqual(merged[0].name, "Cielo lluvioso")

    def test_initial_activity_render_keeps_canonical_group_name(self) -> None:
        jid = "#120363401567622156@whatsapp.xmpp.rayoscompany.com"
        known_group = Chat(jid=jid, name="Desarrollo ⌨️", is_group=True)
        activity_only_group = Chat(
            jid=jid,
            name="#120363401567622156",
            is_group=True,
            last_message_preview="prodesc.org.mx",
            last_message_at=datetime(2026, 7, 13, 16, 35),
        )
        window = SimpleNamespace(
            searchable_chats_by_jid={jid: known_group},
            _merge_chat_lists=MainWindow._merge_chat_lists,
        )

        merged = MainWindow._merge_initial_chat_state(window, [activity_only_group])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].name, "Desarrollo ⌨️")
        self.assertEqual(merged[0].last_message_preview, "prodesc.org.mx")

    def test_cached_human_group_name_becomes_canonical(self) -> None:
        jid = "#120363418240465691@whatsapp.xmpp.rayoscompany.com"
        cached_group = Chat(jid=jid, name="Cielo lluvioso", is_group=True)
        window = SimpleNamespace(
            current_jid="me@example.test",
            message_store=SimpleNamespace(
                load_chats=lambda _account: [cached_group],
                load_latest_messages=lambda _account: [],
            ),
            chat_names_by_jid={},
            _debug_perf=lambda *_args, **_kwargs: None,
            _jid_may_be_group_chat=MainWindow._jid_may_be_group_chat,
            _is_fallback_chat_name=MainWindow._is_fallback_chat_name,
        )

        chats = MainWindow._load_cached_chats(window)

        self.assertEqual(chats[0].name, "Cielo lluvioso")
        self.assertEqual(window.chat_names_by_jid[jid], "Cielo lluvioso")


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


class ContactPresenceLabelTests(unittest.TestCase):
    @staticmethod
    def _window(availability: str, last_seen: datetime | None) -> SimpleNamespace:
        chat_jid = "+15550001234@whatsapp.example.org"
        return SimpleNamespace(
            chat_jid=chat_jid,
            chat_state_by_chat={},
            contact_presence_by_chat={
                chat_jid: SimpleNamespace(
                    availability=availability,
                    status="",
                    last_seen=last_seen,
                )
            },
            _format_presence_time=lambda _value: "hoy 3:41 p. m.",
        )

    def test_last_seen_replaces_away_status_when_timestamp_is_available(self) -> None:
        window = self._window("away", datetime(2026, 7, 16, 15, 41))

        self.assertEqual(
            MainWindow._contact_connection_status_text(window, window.chat_jid),
            "últ. vez hoy 3:41 p. m.",
        )
        self.assertEqual(
            MainWindow._conversation_status_text(window, window.chat_jid),
            "últ. vez hoy 3:41 p. m.",
        )

    def test_away_status_remains_when_privacy_hides_last_seen(self) -> None:
        window = self._window("away", None)

        self.assertEqual(
            MainWindow._contact_connection_status_text(window, window.chat_jid),
            "ausente",
        )
        self.assertEqual(
            MainWindow._conversation_status_text(window, window.chat_jid),
            "contacto ausente",
        )

    def test_last_seen_replaces_stale_available_presence(self) -> None:
        window = self._window("online", datetime(2026, 7, 16, 15, 41))

        self.assertEqual(
            MainWindow._contact_connection_status_text(window, window.chat_jid),
            "últ. vez hoy 3:41 p. m.",
        )
        self.assertEqual(
            MainWindow._conversation_status_text(window, window.chat_jid),
            "últ. vez hoy 3:41 p. m.",
        )

    def test_online_status_is_used_when_presence_has_no_last_seen(self) -> None:
        window = self._window("online", None)

        self.assertEqual(
            MainWindow._contact_connection_status_text(window, window.chat_jid),
            "en línea",
        )

    def test_live_chat_state_replaces_last_seen(self) -> None:
        window = self._window("away", datetime(2026, 7, 16, 15, 41))
        window.chat_state_by_chat[window.chat_jid] = "composing"

        self.assertEqual(
            MainWindow._conversation_status_text(window, window.chat_jid),
            "contacto escribiendo",
        )

    def test_header_uses_live_conversation_status(self) -> None:
        summaries: list[tuple[str, str]] = []
        titles: list[str] = []
        chat = SimpleNamespace(name="Contacto", jid="contact@example.org")
        window = SimpleNamespace(
            conversation=SimpleNamespace(
                current_chat=chat,
                IsShown=lambda: True,
                set_contact_summary=lambda name, status: summaries.append((name, status)),
            ),
            _conversation_status_text=lambda _jid: "contacto escribiendo",
            SetTitle=titles.append,
        )

        MainWindow._refresh_current_chat_status_title(window)

        self.assertEqual(summaries, [("Contacto", "contacto escribiendo")])
        self.assertEqual(titles, [f"{APP_WINDOW_TITLE} - Contacto"])

    def test_conversation_reload_reapplies_saved_presence_status(self) -> None:
        summaries: list[tuple[str, str]] = []
        chat = Chat(jid="contact@example.org", name="Contacto")
        conversation = SimpleNamespace(
            current_chat=chat,
            IsShown=lambda: True,
            set_contact_summary=lambda name, status: summaries.append((name, status)),
            set_messages=lambda _messages, unread_count=0: None,
        )
        window = SimpleNamespace(
            conversation=conversation,
            messages_by_chat={chat.jid: []},
            _load_cached_messages_for_chat=lambda _jid: None,
            _load_cached_group_participants=lambda _chat: None,
            _refresh_conversation_avatar=lambda _chat: None,
            _sync_recording_ui=lambda: None,
            _refresh_load_older_button=lambda _jid: None,
            _conversation_status_text=lambda _jid: "últ. vez hoy 3:41 p. m.",
            _debug_perf=lambda *_args, **_kwargs: None,
        )

        MainWindow._load_conversation(window, chat)

        self.assertEqual(summaries, [("Contacto", "últ. vez hoy 3:41 p. m.")])


class ChatSearchRankingTests(unittest.TestCase):
    def test_exact_chat_name_beats_group_name_and_message_preview(self) -> None:
        exact = Chat(
            jid="+521000000000@example.org",
            name="Burra",
            last_message_preview="aburrida",
        )
        group = Chat(
            jid="#group@example.org",
            name="La familia de la Burra",
            is_group=True,
        )
        preview_only = Chat(
            jid="+522000000000@example.org",
            name="Otro chat",
            last_message_preview="burra",
        )

        terms = MainWindow._search_terms("burra")

        self.assertEqual(MainWindow._chat_search_rank(exact, terms), 0)
        self.assertEqual(MainWindow._chat_search_rank(group, terms), 2)
        self.assertEqual(MainWindow._chat_search_rank(preview_only, terms), 3)

        window = SimpleNamespace(
            _chat_search_rank=MainWindow._chat_search_rank,
            _chat_recency_key=lambda chat: (0, 0, chat.name.casefold()),
        )
        ordered = MainWindow._sort_chats_for_search(
            window,
            [exact, group, preview_only],
            terms,
        )
        self.assertEqual(
            [chat.name for chat in ordered],
            ["Burra", "La familia de la Burra", "Otro chat"],
        )

    def test_chat_name_search_is_accent_insensitive(self) -> None:
        chat = Chat(jid="contact@example.org", name="La familia de la Burrá")

        self.assertEqual(
            MainWindow._chat_search_rank(chat, MainWindow._search_terms("burra")),
            2,
        )

    def test_cached_fallback_name_does_not_hide_known_contact_name(self) -> None:
        jid = "+521000000000@example.org"
        window = SimpleNamespace(
            searchable_chats_by_jid={jid: Chat(jid=jid, name="Burra")},
            chat_names_by_jid={},
            _is_fallback_chat_name=MainWindow._is_fallback_chat_name,
            chat_list=SimpleNamespace(
                chats=lambda: [Chat(jid=jid, name=jid)],
            ),
        )
        window._chat_with_search_name = (
            lambda chat, visible_chat=None: MainWindow._chat_with_search_name(
                window,
                chat,
                visible_chat,
            )
        )

        searchable = MainWindow._searchable_chats_by_jid(window)

        self.assertEqual(searchable[jid].name, "Burra")


class IncomingMessageSoundTests(unittest.TestCase):
    def _window(self, *, active: bool, muted: bool = False) -> SimpleNamespace:
        played: list[str] = []
        return SimpleNamespace(
            IsActive=lambda: active,
            _message_notifications_muted=lambda _message: muted,
            open_chat_message_sound_enabled=True,
            open_chat_message_sound=SimpleNamespace(play=lambda: played.append("open")),
            new_message_sound=SimpleNamespace(play=lambda: played.append("new")),
            played=played,
        )

    @staticmethod
    def _message() -> Message:
        return Message(
            chat_jid="contact@example.org",
            sender_jid="contact@example.org",
            body="Hola",
        )

    def test_uses_open_chat_sound_only_when_window_is_active(self) -> None:
        window = self._window(active=True)

        MainWindow._play_incoming_message_sound(window, self._message(), current_chat_is_open=True)

        self.assertEqual(window.played, ["open"])

    def test_uses_normal_sound_when_open_chat_window_is_not_active(self) -> None:
        window = self._window(active=False)

        MainWindow._play_incoming_message_sound(window, self._message(), current_chat_is_open=True)

        self.assertEqual(window.played, ["new"])

    def test_silenced_chat_plays_no_incoming_sound(self) -> None:
        window = self._window(active=True, muted=True)

        MainWindow._play_incoming_message_sound(window, self._message(), current_chat_is_open=True)

        self.assertEqual(window.played, [])

    def test_disabled_open_chat_sound_plays_no_sound(self) -> None:
        window = self._window(active=True)
        window.open_chat_message_sound_enabled = False

        MainWindow._play_incoming_message_sound(window, self._message(), current_chat_is_open=True)

        self.assertEqual(window.played, [])


class DisplayedMarkerTests(unittest.TestCase):
    def test_reads_synced_displayed_marker_from_sent_carbon(self) -> None:
        emitted: list[object] = []
        stanza = SimpleNamespace(
            xml=ET.fromstring(
                """
                <message from="me@example.org/phone" to="contact@example.org">
                  <displayed xmlns="urn:xmpp:chat-markers:0" id="incoming-id" />
                </message>
                """
            )
        )
        client = SimpleNamespace(
            boundjid=SimpleNamespace(bare="me@example.org"),
            _displayed_marker_id=BridgeXmppClient._displayed_marker_id,
            _emit=emitted.append,
        )

        BridgeXmppClient._emit_synced_displayed_from_outgoing_stanza(client, stanza)

        self.assertEqual(
            emitted,
            [ChatDisplayedSynced(chat_jid="contact@example.org", message_id="incoming-id")],
        )

    def test_reads_group_displayed_state_from_xep_0490(self) -> None:
        message = ET.fromstring(
            """
            <message type="headline">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:mds:displayed:0">
                  <item id="#group@example.org">
                    <displayed xmlns="urn:xmpp:mds:displayed:0">
                      <stanza-id xmlns="urn:xmpp:sid:0" id="room-stanza-id"
                                 by="#group@example.org" />
                    </displayed>
                  </item>
                </items>
              </event>
            </message>
            """
        )

        self.assertEqual(
            BridgeXmppClient._displayed_states_from_xml(message),
            [("#group@example.org", "room-stanza-id")],
        )

    def test_synced_marker_keeps_messages_received_after_it_unread(self) -> None:
        chat = Chat(jid="contact@example.org", name="Contacto", unread_count=2)
        first = Message(
            chat_jid=chat.jid,
            sender_jid=chat.jid,
            body="primero",
            sent_at=datetime.now().astimezone(),
            message_id="first-id",
        )
        outgoing = Message(
            chat_jid=chat.jid,
            sender_jid="me",
            body="respuesta",
            sent_at=first.sent_at + timedelta(seconds=1),
            outgoing=True,
            message_id="outgoing-id",
        )
        second = Message(
            chat_jid=chat.jid,
            sender_jid=chat.jid,
            body="segundo",
            sent_at=first.sent_at + timedelta(seconds=2),
            message_id="second-id",
        )
        window = SimpleNamespace(
            messages_by_chat={chat.jid: [first, outgoing, second]},
            _chat_by_jid=lambda _jid: chat,
            _message_timestamp=MainWindow._message_timestamp,
        )

        self.assertEqual(
            MainWindow._displayed_marker_position(window, chat.jid, "first-id")[0],
            1,
        )
        self.assertEqual(
            MainWindow._displayed_marker_position(window, chat.jid, "outgoing-id")[0],
            1,
        )
        self.assertEqual(
            MainWindow._displayed_marker_position(window, chat.jid, "second-id")[0],
            0,
        )
        self.assertIsNone(
            MainWindow._displayed_marker_position(window, chat.jid, "unknown-id")
        )

    def test_applies_only_the_unread_messages_after_synced_marker(self) -> None:
        chat = Chat(jid="contact@example.org", name="Contacto", unread_count=3)
        now = datetime.now().astimezone().timestamp()
        updates: list[tuple[str, int]] = []
        window = SimpleNamespace(
            synced_displayed_marker_ids_by_chat={chat.jid: "first-id"},
            _chat_by_jid=lambda _jid: chat,
            _displayed_marker_position=lambda _jid, _marker_id: (1, now, now + 1),
            _datetime_timestamp=MainWindow._datetime_timestamp,
            _update_chat_summary=lambda chat_jid, unread_count: updates.append(
                (chat_jid, unread_count)
            ),
        )

        MainWindow._apply_synced_chat_displayed(window, chat.jid)

        self.assertEqual(updates, [(chat.jid, 1)])

    def test_bridge_sends_displayed_marker_for_individual_chat(self) -> None:
        calls: list[dict[str, str]] = []
        requested_plugins: list[str] = []

        class ChatMarkers:
            def send_marker(self, **kwargs: str) -> None:
                calls.append(kwargs)

        class Client:
            def __getitem__(self, key: str) -> ChatMarkers:
                requested_plugins.append(key)
                return ChatMarkers()

        BridgeXmppClient.send_displayed_marker(Client(), "contact@example.org", "incoming-id")

        self.assertEqual(requested_plugins, ["xep_0333"])
        self.assertEqual(
            calls,
            [
                {
                    "mto": "contact@example.org",
                    "id": "incoming-id",
                    "marker": "displayed",
                    "mtype": "chat",
                }
            ],
        )

    def test_bridge_sends_groupchat_displayed_marker_for_group(self) -> None:
        calls: list[dict[str, str]] = []

        class ChatMarkers:
            def send_marker(self, **kwargs: str) -> None:
                calls.append(kwargs)

        class Client:
            def __getitem__(self, _key: str) -> ChatMarkers:
                return ChatMarkers()

        BridgeXmppClient.send_displayed_marker(
            Client(),
            "#group@example.org",
            "room-stanza-id",
            is_group=True,
        )

        self.assertEqual(calls[0]["mtype"], "groupchat")
        self.assertEqual(calls[0]["id"], "room-stanza-id")

    def test_service_schedules_displayed_marker(self) -> None:
        calls: list[tuple[str, str, bool]] = []

        class Loop:
            def call_soon_threadsafe(self, callback: object) -> None:
                callback()  # type: ignore[operator]

        service = SimpleNamespace(
            _client=SimpleNamespace(
                send_displayed_marker=lambda chat_jid, message_id, is_group=False: calls.append(
                    (chat_jid, message_id, is_group)
                )
            ),
            _loop=Loop(),
        )

        XmppService.mark_chat_displayed(service, "contact@example.org", "incoming-id")

        self.assertEqual(calls, [("contact@example.org", "incoming-id", False)])

    def test_marks_only_the_latest_received_message_in_individual_chat(self) -> None:
        chat = Chat(jid="contact@example.org", name="Contacto")
        received = Message(
            chat_jid=chat.jid,
            sender_jid=chat.jid,
            body="pendiente",
            sent_at=datetime.now().astimezone(),
            message_id="incoming-id",
        )
        outgoing = Message(
            chat_jid=chat.jid,
            sender_jid="me",
            body="respuesta",
            sent_at=received.sent_at + timedelta(seconds=1),
            outgoing=True,
            message_id="outgoing-id",
        )
        calls: list[tuple[str, str, bool]] = []
        window = SimpleNamespace(
            conversation=SimpleNamespace(current_chat=chat),
            messages_by_chat={chat.jid: [received, outgoing]},
            displayed_marker_ids_by_chat={},
            xmpp=SimpleNamespace(
                mark_chat_displayed=lambda chat_jid, message_id, is_group=False: calls.append(
                    (chat_jid, message_id, is_group)
                )
            ),
            _message_timestamp=MainWindow._message_timestamp,
        )

        MainWindow._mark_chat_displayed(window, chat)
        MainWindow._mark_chat_displayed(window, chat)

        self.assertEqual(calls, [(chat.jid, "incoming-id", False)])

    def test_uses_room_stanza_id_for_group_chats(self) -> None:
        chat = Chat(jid="#group@example.org", name="Grupo", is_group=True)
        message = Message(
            chat_jid=chat.jid,
            sender_jid="member@example.org",
            body="pendiente",
            sent_at=datetime.now().astimezone(),
            message_id="bridge-message-id",
            displayed_marker_id="room-stanza-id",
            chat_is_group=True,
        )
        calls: list[tuple[str, str, bool]] = []
        window = SimpleNamespace(
            conversation=SimpleNamespace(current_chat=chat),
            messages_by_chat={chat.jid: [message]},
            displayed_marker_ids_by_chat={},
            xmpp=SimpleNamespace(
                mark_chat_displayed=lambda chat_jid, marker_id, is_group=False: calls.append(
                    (chat_jid, marker_id, is_group)
                )
            ),
            _message_timestamp=MainWindow._message_timestamp,
        )

        MainWindow._mark_chat_displayed(window, chat)

        self.assertEqual(calls, [(chat.jid, "room-stanza-id", True)])

    def test_reads_room_stanza_id_only_for_the_matching_room(self) -> None:
        message = ET.fromstring(
            """
            <message>
              <stanza-id xmlns="urn:xmpp:sid:0" by="#other@example.org" id="other-id" />
              <stanza-id xmlns="urn:xmpp:sid:0" by="#group@example.org" id="room-id" />
            </message>
            """
        )

        self.assertEqual(
            BridgeXmppClient._room_stanza_id_from_xml(message, "#group@example.org"),
            "room-id",
        )


class WhatsAppPairingCodeTests(unittest.TestCase):
    def test_new_account_uses_register_and_known_account_uses_relogin(self) -> None:
        calls: list[tuple[str, str]] = []
        xmpp = SimpleNamespace(
            request_whatsapp_registration=lambda jid: calls.append(("register", jid)),
            request_whatsapp_relogin=lambda jid: calls.append(("relogin", jid)),
        )
        window = SimpleNamespace(
            whatsapp_component_jid="whatsapp.example.org",
            whatsapp_link_status="needs_registration",
            xmpp=xmpp,
        )

        MainWindow._request_whatsapp_link_command(window)
        window.whatsapp_link_status = "needs_relogin"
        MainWindow._request_whatsapp_link_command(window)

        self.assertEqual(
            calls,
            [
                ("register", "whatsapp.example.org"),
                ("relogin", "whatsapp.example.org"),
            ],
        )

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

    def test_admin_status_message_still_emits_embedded_qr(self) -> None:
        xml = ET.fromstring(
            """
            <message from="whatsapp.example.org" type="chat">
              <body>Scan the following QR code</body>
              <data xmlns="urn:xmpp:bob" type="image/png">iVBORw0KGgo=</data>
            </message>
            """
        )

        class FakeMessage:
            def __init__(self) -> None:
                self.xml = xml

            def __getitem__(self, key: str) -> object:
                return {
                    "type": "chat",
                    "body": "Scan the following QR code",
                    "from": SimpleNamespace(bare="whatsapp.example.org"),
                }[key]

        emitted: list[object] = []
        client = BridgeXmppClient(
            ConnectionSettings(jid="tester@example.org"),
            "password",
            emitted.append,
        )
        client._emit_chat_state_from_message = lambda *_args: None
        client._emit_inbox_entry = lambda *_args: self.fail(
            "An admin QR must not be added to the inbox"
        )
        client._group_chats_from_bookmark_event_stanza = lambda *_args: []
        client._group_chat_from_invite_stanza = lambda *_args: None
        client._message_retraction_from_stanza = lambda *_args, **_kwargs: None

        client._on_message(FakeMessage())

        self.assertTrue(any(isinstance(event, WhatsAppBridgeStatus) for event in emitted))
        self.assertTrue(
            any(isinstance(event, WhatsAppQrImageDataReceived) for event in emitted)
        )
        if not client.loop.is_closed():
            client.loop.close()

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


class InitialConnectionFlowTests(unittest.TestCase):
    def test_session_start_emits_xmpp_connected_before_roster_finishes(self) -> None:
        async def scenario() -> tuple[list[object], list[str]]:
            roster_release = asyncio.Event()
            events: list[object] = []
            calls: list[str] = []

            async def enable_carbons() -> None:
                calls.append("carbons")

            async def load_initial_roster(_session_generation: int) -> None:
                calls.append("roster-started")
                await roster_release.wait()
                calls.append("roster-finished")

            client = SimpleNamespace(
                _session_started_at=None,
                _last_whatsapp_status_by_component={},
                _initial_roster_chats=None,
                _whatsapp_session_ready=True,
                _initial_remote_sync_started=True,
                _session_generation=0,
                _joined_group_chat_jids={"#room@example.org"},
                _presence_subscription_jids={"contact@example.org"},
                send_presence=lambda: calls.append("presence"),
                _emit=events.append,
                _enable_carbons=enable_carbons,
                _load_initial_roster=load_initial_roster,
            )

            await BridgeXmppClient._on_session_start(client, None)
            await asyncio.sleep(0)
            self.assertTrue(any(isinstance(event, XmppConnected) for event in events))
            self.assertIn("roster-started", calls)
            self.assertNotIn("roster-finished", calls)
            self.assertFalse(client._whatsapp_session_ready)
            self.assertFalse(client._initial_remote_sync_started)
            self.assertFalse(client._joined_group_chat_jids)
            self.assertFalse(client._presence_subscription_jids)

            roster_release.set()
            await asyncio.sleep(0)
            return events, calls

        events, calls = asyncio.run(scenario())

        self.assertTrue(any(isinstance(event, XmppConnected) for event in events))
        self.assertIn("roster-finished", calls)

    def test_initial_remote_sync_waits_for_roster_and_live_whatsapp_status(self) -> None:
        async def scenario() -> list[str]:
            calls: list[str] = []

            async def discover(_chats: list[Chat]) -> None:
                calls.append("groups")

            async def inbox() -> None:
                calls.append("inbox")

            async def displayed() -> None:
                calls.append("displayed")

            client = SimpleNamespace(
                _whatsapp_session_ready=False,
                _initial_roster_chats=[Chat(jid="contact@example.org", name="Contacto")],
                _initial_remote_sync_started=False,
                _discover_group_chats_with_retries=discover,
                load_inbox=inbox,
                _load_displayed_states=displayed,
            )

            BridgeXmppClient._start_initial_remote_sync_if_ready(client)
            await asyncio.sleep(0)
            self.assertFalse(calls)

            client._whatsapp_session_ready = True
            BridgeXmppClient._start_initial_remote_sync_if_ready(client)
            BridgeXmppClient._start_initial_remote_sync_if_ready(client)
            await asyncio.sleep(0)
            return calls

        self.assertCountEqual(asyncio.run(scenario()), ["groups", "inbox", "displayed"])

    def test_stale_roster_result_is_ignored_after_reconnect(self) -> None:
        events: list[object] = []
        client = SimpleNamespace(
            _session_generation=2,
            get_roster=lambda: asyncio.sleep(0),
            _build_roster_chats=lambda: self.fail("A stale roster must not be built"),
            _emit=events.append,
            _debug_whatsapp=lambda _message: None,
        )

        asyncio.run(BridgeXmppClient._load_initial_roster(client, 1))

        self.assertFalse(events)

    def test_disco_timeout_still_checks_linking_commands(self) -> None:
        class DiscoPlugin:
            async def get_info(self, **_kwargs: object) -> object:
                raise TimeoutError("disco timeout")

        command_checks: list[str] = []

        async def check_commands(jid: str) -> None:
            command_checks.append(jid)

        client = SimpleNamespace(
            _debug_whatsapp=lambda _message: None,
            _debug_whatsapp_component_commands=check_commands,
            __getitem__=lambda _key: DiscoPlugin(),
        )

        class ClientProxy:
            _debug_whatsapp = staticmethod(client._debug_whatsapp)
            _debug_whatsapp_component_commands = staticmethod(check_commands)

            def __getitem__(self, _key: str) -> DiscoPlugin:
                return DiscoPlugin()

        asyncio.run(
            BridgeXmppClient._debug_whatsapp_component(
                ClientProxy(),
                "whatsapp.example.org",
            )
        )

        self.assertEqual(command_checks, ["whatsapp.example.org"])


class WhatsAppPairingCodeContinuationTests(unittest.TestCase):
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


class PendingWhatsAppUiTests(unittest.TestCase):
    class Control:
        def __init__(self, shown: bool = False) -> None:
            self.enabled = True
            self.shown = shown

        def Enable(self, enabled: bool) -> None:
            self.enabled = enabled

        def IsShown(self) -> bool:
            return self.shown

    def test_conversation_remote_actions_can_be_disabled_while_verifying(self) -> None:
        panel = SimpleNamespace(
            _remote_actions_enabled=True,
            pause_recording_button=self.Control(),
            compose=self.Control(),
            send_button=self.Control(),
            attach_button=self.Control(),
            sticker_button=self.Control(),
            load_older_button=self.Control(),
            view_once_audio=self.Control(),
        )

        ConversationPanel.set_remote_actions_enabled(panel, False)

        self.assertFalse(panel._remote_actions_enabled)
        self.assertFalse(panel.compose.enabled)
        self.assertFalse(panel.send_button.enabled)
        self.assertFalse(panel.attach_button.enabled)
        self.assertFalse(panel.sticker_button.enabled)
        self.assertFalse(panel.load_older_button.enabled)

    def test_chat_cannot_open_before_live_whatsapp_confirmation(self) -> None:
        placeholders: list[str] = []
        statuses: list[str] = []
        window = SimpleNamespace(
            whatsapp_verified=False,
            _show_chat_placeholder=placeholders.append,
            status_bar=SimpleNamespace(SetStatusText=statuses.append),
        )

        MainWindow._show_selected_chat(window)

        self.assertEqual(
            placeholders,
            ["Verificando confirmación de conexión con WhatsApp..."],
        )
        self.assertEqual(statuses, placeholders)

    def test_cached_chats_are_applied_only_after_whatsapp_confirmation(self) -> None:
        calls: list[str] = []
        window = SimpleNamespace(
            whatsapp_verified=False,
            pending_roster_chats=None,
            _apply_cached_chats_after_whatsapp_verified=lambda: calls.append("cache"),
            _debug_perf=lambda *_args, **_kwargs: None,
        )

        MainWindow._apply_pending_roster_if_ready(window)
        self.assertFalse(calls)

        window.whatsapp_verified = True
        MainWindow._apply_pending_roster_if_ready(window)
        self.assertEqual(calls, ["cache"])

    def test_unverified_whatsapp_does_not_send_displayed_marker(self) -> None:
        sent: list[object] = []
        window = SimpleNamespace(
            whatsapp_verified=False,
            xmpp=SimpleNamespace(
                mark_chat_displayed=lambda *args, **kwargs: sent.append((args, kwargs))
            ),
        )

        MainWindow._mark_chat_displayed(
            window,
            Chat(jid="contact@example.org", name="Contacto"),
        )

        self.assertFalse(sent)


class WhatsAppRegistrationTests(unittest.TestCase):
    class CommandResult:
        def __init__(self, status: str, session_id: str, form: str = "") -> None:
            self.xml = ET.fromstring(
                "<iq><command xmlns='http://jabber.org/protocol/commands'>"
                f"{form}</command></iq>"
            )
            self.command = {"status": status, "sessionid": session_id}

        def __getitem__(self, key: str) -> object:
            if key != "command":
                raise KeyError(key)
            return self.command

    class CommandPlugin:
        def __init__(self, results: list[object]) -> None:
            self.results = results
            self.calls: list[dict[str, object]] = []

        async def send_command(self, jid: str, node: str, **kwargs: object) -> object:
            self.calls.append({"jid": jid, "node": node, **kwargs})
            result = self.results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

    class Client:
        request_whatsapp_registration = BridgeXmppClient.request_whatsapp_registration
        _form_reply_with_defaults = staticmethod(
            BridgeXmppClient._form_reply_with_defaults
        )
        _command_result_text = staticmethod(BridgeXmppClient._command_result_text)
        _means_already_registered = staticmethod(
            BridgeXmppClient._means_already_registered
        )

        def __init__(self, plugin: object) -> None:
            self.plugin = plugin
            self.statuses: list[tuple[str, str, str]] = []
            self.events: list[object] = []
            self.relogin_requests: list[str] = []

        def __getitem__(self, key: str) -> object:
            if key != "xep_0050":
                raise KeyError(key)
            return self.plugin

        def _emit_whatsapp_status(self, jid: str, status: str, detail: str = "") -> None:
            self.statuses.append((jid, status, detail))

        def _emit(self, event: object) -> None:
            self.events.append(event)

        async def request_whatsapp_relogin(self, jid: str) -> None:
            self.relogin_requests.append(jid)

    def test_registration_submits_all_slidge_forms_with_defaults(self) -> None:
        initial_form = "<x xmlns='jabber:x:data' type='form'/>"
        preferences_form = """
            <x xmlns='jabber:x:data' type='form'>
              <field var='sync_presence' type='boolean'><value>true</value></field>
              <field var='reaction_fallback' type='boolean'><value>false</value></field>
            </x>
        """
        plugin = self.CommandPlugin(
            [
                self.CommandResult("executing", "register-1", initial_form),
                self.CommandResult("executing", "register-1", preferences_form),
                self.CommandResult("completed", "register-1"),
            ]
        )
        client = self.Client(plugin)

        asyncio.run(client.request_whatsapp_registration("whatsapp.example.org"))

        actions = [call.get("action", "execute") for call in plugin.calls]
        self.assertEqual(actions, ["execute", "next", "next"])
        self.assertEqual(plugin.calls[1]["sessionid"], "register-1")
        submitted_preferences = plugin.calls[2]["payload"]
        self.assertEqual(
            submitted_preferences["values"],
            {"sync_presence": "true", "reaction_fallback": "false"},
        )
        self.assertEqual(client.statuses[-1][1], "needs_qr")
        self.assertFalse(client.events)

    def test_registration_race_falls_back_to_relogin(self) -> None:
        plugin = self.CommandPlugin(
            [RuntimeError("This is only available for non-users. Unregister first.")]
        )
        client = self.Client(plugin)

        asyncio.run(client.request_whatsapp_registration("whatsapp.example.org"))

        self.assertEqual(client.relogin_requests, ["whatsapp.example.org"])
        self.assertFalse(client.events)


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
    def test_existing_group_keeps_its_name_when_message_arrives(self) -> None:
        group = Chat(
            jid="#5214492757727-1485039809@whatsapp.example.org",
            name="La familia de la Burra",
            is_group=True,
        )
        upserted = []
        window = SimpleNamespace(
            _chat_by_jid=lambda _jid: group,
            chat_list=SimpleNamespace(
                has_chat=lambda _jid: False,
                upsert_chat=upserted.append,
            ),
        )
        message = Message(
            chat_jid=group.jid,
            sender_jid="+5214495380505@whatsapp.example.org",
            sender_name="Jessy Herrera",
            body="Soy yo jaja con la santa muerte",
            chat_is_group=True,
        )

        MainWindow._ensure_chat_for_message(window, message)

        self.assertEqual(upserted, [group])

    def test_joined_group_emits_cached_roster_in_one_event(self) -> None:
        events = []

        class FakeMuc:
            @staticmethod
            def get_joined_rooms() -> list[str]:
                return ["#room@whatsapp.example.org"]

            def get_roster(self, _group_jid: str) -> list[str]:
                return ["Jessy Herrera", "Ángel Alcantar"]

            def get_jid_property(self, _group_jid: str, nick: str, _property: str) -> str:
                return {
                    "Jessy Herrera": "+5214495380505@whatsapp.example.org",
                    "Ángel Alcantar": "+5218126462159@whatsapp.example.org",
                }[nick]

        class FakeClient:
            def __getitem__(self, _key: str) -> FakeMuc:
                return FakeMuc()

            @staticmethod
            def _emit(event: object) -> None:
                events.append(event)

        BridgeXmppClient._emit_group_participants_from_roster(
            FakeClient(),
            "#room@whatsapp.example.org",
        )

        self.assertEqual(
            events,
            [
                GroupParticipantsLoaded(
                    "#room@whatsapp.example.org",
                    [
                        GroupParticipant(
                            "#room@whatsapp.example.org",
                            "+5214495380505@whatsapp.example.org",
                            "Jessy Herrera",
                        ),
                        GroupParticipant(
                            "#room@whatsapp.example.org",
                            "+5218126462159@whatsapp.example.org",
                            "Ángel Alcantar",
                        ),
                    ],
                )
            ],
        )

    def test_group_presence_remembers_real_jid_and_muc_nick(self) -> None:
        events = []
        client = SimpleNamespace(
            _emit=events.append,
            _muc_user_item_jid=BridgeXmppClient._muc_user_item_jid,
            _jid_resource=BridgeXmppClient._jid_resource,
        )
        presence = SimpleNamespace(
            xml=ET.fromstring(
                """
                <presence xmlns="jabber:client" from="#room@whatsapp.example.org/Jessy Herrera">
                  <x xmlns="http://jabber.org/protocol/muc#user">
                    <item jid="+5214495380505@whatsapp.example.org" />
                  </x>
                </presence>
                """
            )
        )

        BridgeXmppClient._emit_group_participant_from_presence(
            client,
            "#room@whatsapp.example.org",
            "#room@whatsapp.example.org/Jessy Herrera",
            presence,
        )

        self.assertEqual(
            events,
            [
                GroupParticipantUpdated(
                    GroupParticipant(
                        group_jid="#room@whatsapp.example.org",
                        jid="+5214495380505@whatsapp.example.org",
                        nick="Jessy Herrera",
                    )
                )
            ],
        )

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

    def test_group_sender_does_not_promote_group_nick_to_global_contact_name(self) -> None:
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

        self.assertNotIn(sender_jid, window.chat_names_by_jid)

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

    def test_reply_quote_is_hydrated_from_referenced_message(self) -> None:
        sent_at = datetime.now().astimezone()
        quoted = Message(
            chat_jid="contact@example.org",
            sender_jid="me",
            body="mensaje original",
            sent_at=sent_at,
            outgoing=True,
            message_id="quoted-id",
        )
        reply = Message(
            chat_jid=quoted.chat_jid,
            sender_jid="contact@example.org",
            body="respuesta",
            sent_at=sent_at + timedelta(seconds=1),
            message_id="reply-id",
            reply_to_id="quoted-id",
        )

        hydrated = MainWindow._hydrate_reply_quotes([quoted, reply])

        self.assertEqual(reply.reply_quote, "mensaje original")
        self.assertEqual(hydrated, [reply])

    def test_reply_quote_is_hydrated_when_older_target_arrives_later(self) -> None:
        sent_at = datetime.now().astimezone()
        reply = Message(
            chat_jid="contact@example.org",
            sender_jid="contact@example.org",
            body="respuesta",
            sent_at=sent_at,
            message_id="reply-id",
            reply_to_id="quoted-id",
        )
        quoted = Message(
            chat_jid=reply.chat_jid,
            sender_jid="me",
            body="mensaje cargado después",
            sent_at=sent_at - timedelta(seconds=1),
            outgoing=True,
            message_id="quoted-id",
        )

        self.assertEqual(MainWindow._hydrate_reply_quotes([reply]), [])
        hydrated = MainWindow._hydrate_reply_quotes([reply, quoted])

        self.assertEqual(reply.reply_quote, "mensaje cargado después")
        self.assertEqual(hydrated, [reply])

    def test_explicit_reply_quote_is_not_replaced(self) -> None:
        quoted = Message(
            chat_jid="contact@example.org",
            sender_jid="me",
            body="texto local distinto",
            message_id="quoted-id",
        )
        reply = Message(
            chat_jid=quoted.chat_jid,
            sender_jid="contact@example.org",
            body="respuesta",
            message_id="reply-id",
            reply_quote="cita enviada por el bridge",
            reply_to_id="quoted-id",
        )

        hydrated = MainWindow._hydrate_reply_quotes([quoted, reply])

        self.assertEqual(reply.reply_quote, "cita enviada por el bridge")
        self.assertEqual(hydrated, [])

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
        self.assertEqual(
            [message.message_id for message in messages],
            [first.message_id, second.message_id],
        )

    def test_message_correction_replaces_original_message(self) -> None:
        original = Message(
            chat_jid="+5218126462159@whatsapp.example.org",
            sender_jid="me",
            body="texto con error",
            sent_at=datetime.now().astimezone(),
            outgoing=True,
            message_id="original-id",
            reply_quote="cita original",
            reply_to_jid="contact@example.org",
            reply_to_id="quoted-id",
        )
        correction = Message(
            chat_jid=original.chat_jid,
            sender_jid="me",
            body="texto corregido",
            sent_at=original.sent_at + timedelta(seconds=5),
            outgoing=True,
            message_id="correction-id",
            replaces_id="original-id",
            reply_to_jid="contact@example.org",
            reply_to_id="quoted-id",
        )

        self.assertTrue(MainWindow._apply_message_correction([original], correction))
        self.assertEqual(original.body, "texto corregido")
        self.assertTrue(original.edited)
        self.assertEqual(original.reply_quote, "cita original")

    def test_delivery_state_does_not_go_backwards(self) -> None:
        self.assertEqual(MainWindow._merge_delivery_state("delivered", "sent"), "delivered")
        self.assertEqual(MainWindow._merge_delivery_state("read", "delivered"), "read")
        self.assertEqual(MainWindow._merge_delivery_state("pending", "sent"), "sent")

    def test_history_state_keeps_known_delivery_state(self) -> None:
        chat_jid = "+5215555555555@whatsapp.example.org"
        message = Message(
            chat_jid=chat_jid,
            sender_jid="Yo",
            body="historial",
            sent_at=datetime.now().astimezone(),
            outgoing=True,
            message_id="message-1",
            delivery_state="sent",
        )

        class MergeHarness:
            _message_timestamp = staticmethod(MainWindow._message_timestamp)
            _message_merge_key = staticmethod(MainWindow._message_merge_key)
            _matching_group_self_echo_index = staticmethod(
                MainWindow._matching_group_self_echo_index
            )
            _matching_content_message_index = staticmethod(
                MainWindow._matching_content_message_index
            )
            _message_content_key = staticmethod(MainWindow._message_content_key)
            _merge_message_metadata = staticmethod(MainWindow._merge_message_metadata)

            def __init__(self) -> None:
                self.messages_by_chat: dict[str, list[Message]] = {}
                self.delivery_states_by_message = {(chat_jid, "message-1"): "delivered"}

        window = MergeHarness()
        MainWindow._merge_messages(window, chat_jid, [message])

        self.assertEqual(window.messages_by_chat[chat_jid][0].delivery_state, "delivered")

    def test_audio_autoplay_stops_at_non_audio_message(self) -> None:
        audio_one = Message(
            chat_jid="chat@example.org",
            sender_jid="contact@example.org",
            body="",
            audio_url="https://example.org/one.ogg",
        )
        text = Message(
            chat_jid="chat@example.org",
            sender_jid="contact@example.org",
            body="intermedio",
        )
        audio_two = Message(
            chat_jid="chat@example.org",
            sender_jid="contact@example.org",
            body="",
            audio_url="https://example.org/two.ogg",
        )
        panel = ConversationPanel.__new__(ConversationPanel)
        panel._message_rows = [audio_one, text, audio_two]

        self.assertEqual(panel._next_audio_message(1), (None, None))

    def test_reads_message_correction_target_id(self) -> None:
        xml = ET.fromstring(
            """
            <message>
                <replace xmlns="urn:xmpp:message-correct:0" id="original-id" />
            </message>
            """
        )

        self.assertEqual(
            BridgeXmppClient._message_correction_id_from_xml(xml),
            "original-id",
        )

    def test_reads_reply_metadata_for_message_edit(self) -> None:
        xml = ET.fromstring(
            """
            <message>
                <reply xmlns="urn:xmpp:reply:0" to="contact@example.org" id="quoted-id" />
            </message>
            """
        )

        self.assertEqual(BridgeXmppClient._reply_to_jid_from_xml(xml), "contact@example.org")
        self.assertEqual(BridgeXmppClient._reply_to_id_from_xml(xml), "quoted-id")


if __name__ == "__main__":
    unittest.main()
