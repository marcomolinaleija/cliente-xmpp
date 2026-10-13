from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from xml.etree import ElementTree as ET

from cliente_xmpp.models.chat import Chat
from cliente_xmpp.models.mentions import (
    GroupParticipant,
    MentionCandidate,
    MentionReference,
    active_mention_query,
    matching_mention_candidates,
    mention_references_in_text,
    readable_mention_nick,
)
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.xmpp.client import XmppService


class MentionQueryTests(unittest.TestCase):
    def test_detects_query_at_caret(self) -> None:
        self.assertEqual(active_mention_query("Hola @Jes", 9), (5, 9, "Jes"))

    def test_does_not_treat_email_as_a_mention(self) -> None:
        self.assertIsNone(active_mention_query("correo@ejemplo", 14))

    def test_matches_personalized_and_whatsapp_names_without_accents(self) -> None:
        candidates = [
            MentionCandidate(
                participant_jid="+521@whatsapp.example.org",
                display_name="Burra",
                mention_text="Jessy Herrera",
            ),
            MentionCandidate(
                participant_jid="+522@whatsapp.example.org",
                display_name="Ángel",
                mention_text="Angel Alcantar",
            ),
        ]

        self.assertEqual(
            matching_mention_candidates(candidates, "jess")[0].participant_jid,
            "+521@whatsapp.example.org",
        )
        self.assertEqual(
            matching_mention_candidates(candidates, "angel")[0].participant_jid,
            "+522@whatsapp.example.org",
        )

        self.assertEqual(
            mention_references_in_text("Hola Jessy Herrera y Angel Alcantar", candidates),
            [
                MentionReference("+521@whatsapp.example.org", 5, 18),
                MentionReference("+522@whatsapp.example.org", 21, 35),
            ],
        )

    def test_adds_standard_reference_to_outbound_stanza(self) -> None:
        message = ET.Element("message")

        XmppService._append_mentions(
            message,
            [MentionReference("+521@whatsapp.example.org", 5, 18)],
        )

        self.assertEqual(
            ET.tostring(message, encoding="unicode"),
            '<message xmlns:ns0="urn:xmpp:reference:0"><ns0:reference '
            'type="mention" uri="xmpp:+521@whatsapp.example.org" begin="5" end="18" />'
            "</message>",
        )

    def test_mention_ranges_use_unicode_code_points(self) -> None:
        candidate = MentionCandidate(
            participant_jid="+521@whatsapp.example.org",
            display_name="Jessy",
            mention_text="Jessy",
        )

        self.assertEqual(
            mention_references_in_text("👋 Hola Jessy", [candidate]),
            [MentionReference(candidate.participant_jid, 7, 12)],
        )


class GroupParticipantStoreTests(unittest.TestCase):
    def test_persists_latest_group_nick(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "messages.sqlite3")
            participant = GroupParticipant(
                group_jid="#grupo@whatsapp.example.org",
                jid="+521@whatsapp.example.org",
                nick="Jessy Herrera",
            )
            store.upsert_group_participant("me@example.org", participant)
            store.upsert_group_participant(
                "me@example.org",
                GroupParticipant(
                    group_jid=participant.group_jid,
                    jid=participant.jid,
                    nick="Jessy H.",
                ),
            )

            self.assertEqual(
                store.load_group_participants("me@example.org", participant.group_jid),
                [
                    GroupParticipant(
                        group_jid=participant.group_jid,
                        jid=participant.jid,
                        nick="Jessy H.",
                    )
                ],
            )


class MentionPresentationTests(unittest.TestCase):
    def test_canonical_punycode_symbols_are_readable_without_changing_ordinary_names(self):
        for human in ("Fixture Person 💙", "Fixture Heart ❤️", "Fixture Cat 🐈‍⬛", "Fixture é💙"):
            with self.subTest(human=human):
                raw = human.encode("punycode").decode("ascii")
                self.assertEqual(readable_mention_nick(raw), human)
                self.assertEqual(readable_mention_nick(human), human)
        for nick in ("Fixture Person", "Team-123", "Fixture-dev", "xn--test", "malformed-!", ""):
            with self.subTest(nick=nick):
                self.assertEqual(readable_mention_nick(nick), nick)
        # An unmarked, emoji-only name needs corroboration from the contact label.
        raw = "💙".encode("punycode").decode("ascii")
        self.assertEqual(readable_mention_nick(raw), raw)
        self.assertEqual(readable_mention_nick(raw, "💙"), "💙")
        for human in ("Fixture\x00💙", "Fixture\ud800💙"):
            raw = human.encode("punycode").decode("ascii")
            self.assertEqual(readable_mention_nick(raw, human), raw)

    def test_selection_inserts_emoji_and_keeps_real_jid_unicode_ranges_and_old_drafts(self):
        human = "Fixture Person 💙"
        raw = human.encode("punycode").decode("ascii")
        participant = GroupParticipant("#fixture@example.test", "member@example.test", raw)
        window = MainWindow.__new__(MainWindow)
        window.current_jid = "owner@example.test"
        window.group_participants_by_chat = {participant.group_jid: {participant.jid: participant}}
        window._display_name_for_jid = Mock(return_value="Local fixture alias 💙")
        window._fallback_display_name_for_jid = Mock(return_value="member")
        candidates = window._mention_candidates_for_chat(participant.group_jid)
        self.assertEqual(candidates[0].mention_text, human)
        self.assertNotIn(raw, candidates[0].label)
        self.assertEqual(participant.nick, raw)
        body = "👋 @Fix"
        replacement = Mock()
        window.conversation = SimpleNamespace(
            selected_mention_suggestion_index=lambda: 0,
            compose=SimpleNamespace(GetValue=lambda: body, GetInsertionPoint=lambda: len(body)),
            mention_insertion_point=lambda: len(body),
            replace_mention_query=replacement,
        )
        window.status_bar = SimpleNamespace(SetStatusText=Mock())
        window.speaker = SimpleNamespace(speak=Mock())
        window.mention_candidates = candidates
        self.assertTrue(window._accept_selected_mention())
        replacement.assert_called_once_with(2, len(body), human + " ")
        sent = "👋 " + human + " "
        references = window._mention_references_for_message(
            Chat(participant.group_jid, "Fixture", is_group=True), sent,
        )
        self.assertEqual(references, [MentionReference(participant.jid, 2, 2 + len(human))])
        xml = ET.Element("message")
        XmppService._append_mentions(xml, references)
        reference = xml.find("{urn:xmpp:reference:0}reference")
        self.assertEqual(reference.get("uri"), "xmpp:" + participant.jid)
        self.assertEqual(sent[int(reference.get("begin")):int(reference.get("end"))], human)
        old = "Hello " + raw
        self.assertEqual(mention_references_in_text(old, candidates), [
            MentionReference(participant.jid, 6, len(old)),
        ])
        window._display_name_for_jid = Mock(return_value="member")
        self.assertEqual(window._mention_candidates_for_chat(participant.group_jid)[0].label, human)
