from __future__ import annotations

import unittest
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from cliente_xmpp.config.settings import ConnectionSettings
from cliente_xmpp.xmpp.client import BridgeXmppClient
from cliente_xmpp.xmpp.events import (
    ChatReadFailed,
    MessageDeliveryUpdated,
    WhatsAppBridgeStatus,
    XmppError,
)


class ReadMarkerErrorTests(unittest.TestCase):
    def handle(self, payload: str, reason: str = "fixture read failure", *, pending=None):
        emitted = []
        statuses = []
        client = SimpleNamespace(
            _emit=emitted.append,
            _pending_read_markers=pending or {},
            _message_error_parts=BridgeXmppClient._message_error_parts,
            _retry_legacy_session_message=lambda *_: False,
            _jid_may_be_group_chat=lambda _: False,
            _is_probable_whatsapp_bridge_jid=lambda _: True,
            _emit_whatsapp_status=lambda component, status, text: statuses.append(
                WhatsAppBridgeStatus(status, component, text)
            ),
        )
        message = SimpleNamespace(
            xml=ET.fromstring(
                '<message xmlns="jabber:client" type="error">' + payload + '<error type="cancel">'
                '<internal-server-error xmlns="urn:ietf:params:xml:ns:xmpp-stanzas"/>'
                + '<text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">'
                + reason
                + "</text></error></message>"
            )
        )

        class Stanza(dict):
            xml = message.xml

        BridgeXmppClient._handle_message_error(
            client, Stanza(id="fixture-envelope"), "#fixture@whatsapp.example.test"
        )
        return emitted, statuses

    def test_standalone_displayed_error_is_not_a_send_failure(self):
        for hints in (
            "",
            '<store xmlns="urn:xmpp:hints"/>',
            '<origin-id xmlns="urn:xmpp:sid:0" id="fixture"/>',
        ):
            with self.subTest(hints=hints):
                emitted, _ = self.handle(
                    '<displayed xmlns="urn:xmpp:chat-markers:0" id="fixture"/>' + hints
                )
                self.assertEqual(len(emitted), 1)
                self.assertIsInstance(emitted[0], ChatReadFailed)
                self.assertEqual(emitted[0].chat_jid, "#fixture@whatsapp.example.test")
                self.assertIn("fixture read failure", emitted[0].detail)

    def test_real_and_mixed_payload_errors_keep_delivery_failure(self):
        for payload in (
            "<body>Fixture</body>",
            "",
            '<displayed xmlns="urn:xmpp:chat-markers:0" id="fixture"/><body>Fixture</body>',
            '<displayed xmlns="urn:xmpp:chat-markers:0" id="fixture"/><x xmlns="jabber:x:oob"/>',
        ):
            with self.subTest(payload=payload):
                emitted, _ = self.handle(payload)
                self.assertTrue(any(isinstance(event, MessageDeliveryUpdated) for event in emitted))
                self.assertTrue(any(isinstance(event, XmppError) for event in emitted))
                self.assertFalse(any(isinstance(event, ChatReadFailed) for event in emitted))

    def test_unauthenticated_marker_still_requires_relogin(self):
        emitted, statuses = self.handle(
            '<displayed xmlns="urn:xmpp:chat-markers:0" id="fixture"/>', "unauthenticated session"
        )
        self.assertEqual(emitted, [])
        self.assertEqual([status.status for status in statuses], ["needs_relogin"])

    def test_stripped_marker_requires_exact_unexpired_envelope_and_chat(self):
        import time

        for entry, classified in (
            (("#fixture@whatsapp.example.test", time.monotonic() + 120), True),
            (("#other@whatsapp.example.test", time.monotonic() + 120), False),
            (("#fixture@whatsapp.example.test", 0), False),
        ):
            with self.subTest(entry=entry):
                emitted, _ = self.handle("", pending={"fixture-envelope": entry})
                self.assertEqual(any(isinstance(e, ChatReadFailed) for e in emitted), classified)
                self.assertEqual(any(isinstance(e, XmppError) for e in emitted), not classified)
        emitted, _ = self.handle(
            "<body>Fixture user text</body>",
            pending={
                "fixture-envelope": ("#fixture@whatsapp.example.test", time.monotonic() + 120)
            },
        )
        self.assertTrue(any(isinstance(e, MessageDeliveryUpdated) for e in emitted))


class NativeReadMarkerErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_error_loses_marker_but_duplicate_callbacks_correlate(
        self,
    ):
        from unittest.mock import patch

        from slixmpp import ComponentXMPP
        from slixmpp.exceptions import XMPPError
        from slixmpp.stanza import Message

        emitted = []
        client = BridgeXmppClient(
            ConnectionSettings(jid="reader@example.test"), "fixture", emitted.append
        )
        client.register_plugin("xep_0333")
        with patch.object(client, "send") as send:
            client.send_displayed_marker("#fixture@whatsapp.example.test", "fixture-horizon", True)
            marker = send.call_args.args[0]
            envelope_id = marker["id"]
            self.assertTrue(envelope_id.startswith("can-read-"))
            self.assertEqual(marker["displayed"]["id"], "fixture-horizon")
        # Error replies originate at a component, not the sending client. A
        # client reply intentionally strips its own from= attribute.
        component = ComponentXMPP("whatsapp.example.test", "fixture", "localhost", 5347)
        request = Message(stream=component, xml=ET.fromstring(ET.tostring(marker.xml)))
        with patch.object(component, "send") as send:
            request.exception(XMPPError("internal-server-error", "Fixture pending read"))
            reply = send.call_args.args[0]
        self.assertEqual(reply["id"], envelope_id)
        self.assertEqual(str(reply["from"].bare), "#fixture@whatsapp.example.test")
        self.assertIsNone(reply.xml.find("{urn:xmpp:chat-markers:0}displayed"))
        client._handle_message_error(reply)
        client._handle_message_error(reply)
        self.assertEqual(len(emitted), 2)
        self.assertTrue(all(isinstance(event, ChatReadFailed) for event in emitted))
        # The bridge now preserves the payload too, which also protects a
        # running client that does not yet have the envelope-ticket changes.
        request = Message(stream=component, xml=ET.fromstring(ET.tostring(marker.xml)))
        with patch.object(component, "send") as send:
            request.exception(XMPPError("item-not-found", "Fixture horizon missing", clear=False))
            preserved = send.call_args.args[0]
        client._pending_read_markers.clear()
        self.assertIsNotNone(preserved.xml.find("{urn:xmpp:chat-markers:0}displayed"))
        client._handle_message_error(preserved)
        self.assertEqual(len(emitted), 3)
        self.assertIsInstance(emitted[-1], ChatReadFailed)

    async def test_pending_markers_are_bounded_and_send_failure_does_not_leave_a_ticket(self):
        from unittest.mock import patch

        client = BridgeXmppClient(
            ConnectionSettings(jid="reader@example.test"), "fixture", lambda _: None
        )
        client.register_plugin("xep_0333")
        with patch.object(client, "send"):
            for _ in range(260):
                client.send_displayed_marker(
                    "#fixture@whatsapp.example.test", "fixture-horizon", True
                )
        self.assertEqual(len(client._pending_read_markers), 256)
        previous = dict(client._pending_read_markers)
        with (
            patch.object(client, "send", side_effect=RuntimeError("fixture send failure")),
            self.assertRaises(RuntimeError),
        ):
            client.send_displayed_marker("#fixture@whatsapp.example.test", "fixture-horizon", True)
        self.assertLessEqual(len(client._pending_read_markers), 256)
        self.assertTrue(set(client._pending_read_markers).issubset(previous))


if __name__ == "__main__":
    unittest.main()
