from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from cliente_xmpp.models.mentions import (
    GroupParticipant,
    MentionCandidate,
    MentionReference,
    active_mention_query,
    matching_mention_candidates,
    mention_references_in_text,
)
from cliente_xmpp.storage.message_store import MessageStore
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
