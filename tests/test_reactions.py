from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock
from xml.etree import ElementTree as ET

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.models.reactions import ReactionState, ReactionUpdate, is_supported_reaction
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.ui.chat_list_panel import ChatListPanel
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.ui.reaction_dialog import matching_reactions
from cliente_xmpp.xmpp.client import REACTIONS_NS, BridgeXmppClient


class ReactionParsingTests(unittest.TestCase):
    def test_xep_0444_reaction_is_parsed_without_a_body(self) -> None:
        xml = ET.fromstring(
            f"<message xmlns='jabber:client'><reactions xmlns='{REACTIONS_NS}' id='original-id'>"
            "<reaction>❤️</reaction><reaction>👍</reaction></reactions></message>"
        )

        update = BridgeXmppClient._reaction_update_from_xml(
            chat_jid="ari@example.test",
            xml=xml,
            sender_id="ari@example.test",
            sent_at=datetime(2026, 8, 14),
        )

        self.assertIsNotNone(update)
        assert update is not None
        self.assertEqual(update.target_id, "original-id")
        self.assertEqual(update.reactions, ("❤️", "👍"))

    def test_empty_reaction_set_means_the_sender_removed_their_reaction(self) -> None:
        xml = ET.fromstring(
            f"<message xmlns='jabber:client'><reactions xmlns='{REACTIONS_NS}' id='original-id' />"
            "</message>"
        )

        update = BridgeXmppClient._reaction_update_from_xml(
            chat_jid="ari@example.test",
            xml=xml,
            sender_id="ari@example.test",
        )

        self.assertIsNotNone(update)
        assert update is not None
        self.assertEqual(update.reactions, ())


class ReactionMergeTests(unittest.TestCase):
    def test_missing_target_never_quotes_the_chat_summary(self) -> None:
        for is_group in (False, True):
            for previous in ("Otro mensaje reciente", "Ari reaccionó a «un texto» con 👍🏻"):
                for reactions in (("👍🏻",), ()):
                    with self.subTest(is_group=is_group, previous=previous, reactions=reactions):
                        chat = Chat(jid="chat@example.invalid", name="Fixture",
                                    is_group=is_group, last_message_preview=previous)
                        window = SimpleNamespace(
                            _display_name_for_jid=lambda _: "Ari",
                            _visible_chat_by_jid=lambda _, chat=chat: chat,
                            searchable_chats_by_jid={chat.jid: chat},
                        )
                        update = ReactionUpdate(chat_jid=chat.jid, target_id="not-loaded",
                                                sender_id="actor@example.invalid",
                                                reactions=reactions, is_group=is_group)
                        expected = ("Ari reaccionó a un mensaje con 👍🏻" if reactions
                                    else "Ari quitó su reacción de un mensaje")
                        for _ in range(3):
                            preview = MainWindow._reaction_preview(window, update, None)
                            self.assertEqual(preview, expected)
                            chat.last_message_preview = preview

    def test_pending_group_reaction_uses_original_text_once_history_arrives(self) -> None:
        chat = Chat(jid="group@example.invalid", name="Fixture group", is_group=True,
                    last_message_preview="Ari reaccionó a «otro texto» con 👍🏻")

        def summary(_jid, **kwargs):
            chat.last_message_preview = kwargs["preview"]

        window = SimpleNamespace(
            messages_by_chat={}, pending_reaction_updates_by_chat={},
            _display_name_for_jid=lambda _: "Ari", _visible_chat_by_jid=lambda _: chat,
            searchable_chats_by_jid={chat.jid: chat}, _persist_messages=Mock(),
            _update_chat_activity=Mock(), _update_chat_summary=summary, _refresh_chat_order=Mock(),
            _datetime_timestamp=MainWindow._datetime_timestamp,
            chat_list=SimpleNamespace(IsShown=lambda: False),
            conversation=SimpleNamespace(current_chat=None),
        )
        for name in ("_handle_reaction_update", "_record_reaction_activity", "_reaction_preview",
                     "_apply_reaction_update", "_flush_pending_reaction_updates"):
            setattr(window, name, MethodType(getattr(MainWindow, name), window))
        for emoji in ("👍🏻", "❤️"):
            window._handle_reaction_update(ReactionUpdate(
                chat_jid=chat.jid, target_id="room-stanza", sender_id="actor@example.invalid",
                reactions=(emoji,), is_group=True, sent_at=datetime(2026, 1, 1),
            ))
            self.assertEqual(chat.last_message_preview, f"Ari reaccionó a un mensaje con {emoji}")
            self.assertEqual(len(window.pending_reaction_updates_by_chat[chat.jid]), 1)
        window._persist_messages.assert_not_called()
        target = Message(chat_jid=chat.jid, sender_jid="author@example.invalid",
                         message_id="bridge-id", displayed_marker_id="room-stanza",
                         body="Texto original", chat_is_group=True)
        window.messages_by_chat[chat.jid] = [target]
        window._flush_pending_reaction_updates(chat.jid)
        self.assertEqual(chat.last_message_preview, "Ari reaccionó a «Texto original» con ❤️")
        self.assertEqual(target.reactions, ("❤️",))
        self.assertEqual(target.body, "Texto original")
        self.assertNotIn(chat.jid, window.pending_reaction_updates_by_chat)
        window._persist_messages.assert_called_once_with([target])

    def test_original_message_that_looks_like_a_reaction_is_not_rewritten(self) -> None:
        body = "Ari reaccionó a «un texto» con 👍🏻"
        target = Message(chat_jid="group@example.invalid", sender_jid="author@example.invalid",
                         body=body, message_id="original")
        window = SimpleNamespace(_display_name_for_jid=lambda _: "Fixture")
        update = ReactionUpdate(chat_jid=target.chat_jid, target_id=target.message_id,
                                sender_id="actor@example.invalid", sender_name="Bea",
                                reactions=("❤️",))
        self.assertEqual(MainWindow._reaction_preview(window, update, target),
                         f"Bea reaccionó a «{body}» con ❤️")
        self.assertEqual(target.body, body)

    def test_new_state_replaces_only_the_same_sender_reactions(self) -> None:
        message = Message(
            chat_jid="ari@example.test",
            sender_jid="me",
            body="Hola",
            message_id="original-id",
            reactions=("👍", "❤️"),
            reaction_states=(
                ReactionState(sender_id="ari@example.test", reactions=("👍",)),
                ReactionState(sender_id="bea@example.test", reactions=("❤️",)),
            ),
        )
        window = SimpleNamespace(messages_by_chat={message.chat_jid: [message]})

        target = MainWindow._apply_reaction_update(
            window,
            ReactionUpdate(
                chat_jid=message.chat_jid,
                target_id="original-id",
                sender_id="ari@example.test",
                reactions=("😂",),
            ),
        )

        self.assertIs(target, message)
        self.assertEqual(message.reactions, ("❤️", "😂"))
        self.assertEqual(
            message.reaction_states,
            (
                ReactionState(sender_id="bea@example.test", reactions=("❤️",)),
                ReactionState(sender_id="ari@example.test", reactions=("😂",)),
            ),
        )

    def test_group_reaction_can_target_the_room_stanza_id(self) -> None:
        message = Message(
            chat_jid="#grupo@example.test",
            sender_jid="me",
            body="Mensaje de grupo",
            message_id="bridge-id",
            displayed_marker_id="room-stanza-id",
        )
        window = SimpleNamespace(messages_by_chat={message.chat_jid: [message]})

        target = MainWindow._apply_reaction_update(
            window,
            ReactionUpdate(
                chat_jid=message.chat_jid,
                target_id="room-stanza-id",
                sender_id="ari@example.test",
                reactions=("🔥",),
                is_group=True,
            ),
        )

        self.assertIs(target, message)
        self.assertEqual(message.reactions, ("🔥",))

    def test_chat_summary_describes_the_reaction_and_its_target(self) -> None:
        message = Message(
            chat_jid="ari@example.test",
            sender_jid="me",
            body="Nos vemos mañana",
            message_id="original-id",
        )
        window = SimpleNamespace(_display_name_for_jid=lambda _jid: "Ari")

        preview = MainWindow._reaction_preview(
            window,
            ReactionUpdate(
                chat_jid=message.chat_jid,
                target_id=message.message_id,
                sender_id="ari@example.test",
                reactions=("❤️",),
            ),
            message,
        )

        self.assertEqual(preview, "Ari reaccionó a «Nos vemos mañana» con ❤️")


class ReactionStorageTests(unittest.TestCase):
    def test_cached_recursive_summary_is_compacted_without_changing_real_messages(self) -> None:
        recursive = "Texto anterior"
        for _ in range(5):
            quote = recursive if len(recursive) <= 80 else recursive[:77] + "..."
            recursive = f"Ari reaccionó a «{quote}» con 👍🏻"
        ordinary = "Ari reaccionó a «Texto original» con ❤️"
        for case, body, preview, offset, expected in (
            ("legacy", "Texto original", recursive, 5, "Ari reaccionó a un mensaje con 👍🏻"),
            ("literal_message", recursive, recursive, 5, recursive),
            ("ordinary_reaction", "Texto original", ordinary, 5, ordinary),
            ("newer_message", "Mensaje nuevo", recursive, -5, "Mensaje nuevo"),
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp_dir:
                store = MessageStore(Path(temp_dir) / "messages.sqlite3")
                account, jid = "me@example.invalid", "#fixture@whatsapp.example.invalid"
                sent_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
                message = Message(chat_jid=jid, sender_jid="author@example.invalid", body=body,
                                  message_id="fixture-message", sent_at=sent_at, chat_is_group=True)
                store.upsert_messages(account, [message])
                chat = Chat(jid=jid, name="Fixture group", is_group=True, unread_count=3,
                            notifications_muted=True, notification_settings_known=True,
                            last_message_preview=preview,
                            last_message_at=sent_at + timedelta(minutes=offset))
                store.upsert_chat(account, chat)
                stored_preview = store.load_chats(account)[0].last_message_preview
                window = SimpleNamespace(
                    current_jid=account, message_store=store, chat_names_by_jid={},
                    _debug_perf=Mock(), _display_name_for_jid=lambda _: "Fixture group",
                    _jid_may_be_group_chat=MainWindow._jid_may_be_group_chat,
                    _is_fallback_chat_name=MainWindow._is_fallback_chat_name,
                    _summary_preview_can_update=MainWindow._summary_preview_can_update,
                    _message_body_for_display=MainWindow._message_body_for_display,
                    _message_timestamp=MainWindow._message_timestamp, _update_chat_activity=Mock(),
                )
                restored = MainWindow._load_cached_chats(window)[0]
                self.assertEqual(restored.last_message_preview, expected)
                self.assertEqual(restored.unread_count, 3)
                self.assertTrue(restored.notifications_muted)
                self.assertEqual(restored.last_message_at,
                                 sent_at if offset < 0 else chat.last_message_at)
                panel = SimpleNamespace(_format_status=lambda _: "",
                                        _format_preview=ChatListPanel._format_preview,
                                        _format_time=ChatListPanel._format_time)
                row = ChatListPanel._format_chat_row(panel, restored)
                self.assertIn(expected, row)
                # Cache recovery is in memory; no destructive database cleanup.
                self.assertEqual(store.load_chats(account)[0].last_message_preview, stored_preview)
                self.assertEqual(store.load_recent_messages(account, jid)[0].body, body)

    def test_reaction_states_survive_a_cache_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            message = Message(
                chat_jid="ari@example.test",
                sender_jid="me",
                body="Hola",
                message_id="original-id",
                reactions=("👍", "❤️"),
                reaction_states=(
                    ReactionState(sender_id="ari@example.test", reactions=("👍",)),
                    ReactionState(sender_id="bea@example.test", reactions=("❤️",)),
                ),
            )

            store.upsert_messages("me@example.test", [message])
            restored = store.load_recent_messages("me@example.test", message.chat_jid, limit=10)

        self.assertEqual(restored[0].reactions, ("👍", "❤️"))
        self.assertEqual(restored[0].reaction_states, message.reaction_states)


class ReactionPickerTests(unittest.TestCase):
    def test_more_reactions_searches_by_spanish_name_and_accepts_an_emoji(self) -> None:
        self.assertIn(("🔥", "fuego"), matching_reactions("fuego"))
        self.assertEqual(matching_reactions("🫶")[0], ("🫶", "usar este emoji"))
        self.assertTrue(is_supported_reaction("🫶"))
        self.assertFalse(is_supported_reaction("reacción"))
