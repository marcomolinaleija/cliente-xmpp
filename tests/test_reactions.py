from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from cliente_xmpp.models.chat import Message
from cliente_xmpp.models.reactions import ReactionState, ReactionUpdate, is_supported_reaction
from cliente_xmpp.storage.message_store import MessageStore
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
