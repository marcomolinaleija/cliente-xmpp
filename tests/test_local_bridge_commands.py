from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from xml.etree import ElementTree as ET

from cliente_xmpp.models.chat import Chat
from cliente_xmpp.models.local_commands import is_local_bridge_command
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.xmpp.client import BridgeXmppClient


class LocalBridgeCommandDetectionTests(unittest.TestCase):
    def test_recognizes_only_commands_consumed_by_the_bridge(self) -> None:
        for body in (
            "/stats",
            " /STATUS ",
            "/transcribe off",
            "/TRANSCRIBE here default",
            "/stats detalle",
        ):
            with self.subTest(body=body):
                self.assertTrue(is_local_bridge_command(body))

        for body in (
            "",
            "hola",
            "/hello",
            "/statistics",
            "/transcription off",
            "texto /stats",
        ):
            with self.subTest(body=body):
                self.assertFalse(is_local_bridge_command(body))


class LocalBridgeCommandSendingTests(unittest.TestCase):
    def test_command_is_sent_ephemerally_without_local_message(self) -> None:
        chat = Chat(jid="#group@example.test", name="Grupo", is_group=True)
        window = MainWindow.__new__(MainWindow)
        window.reply_context = None
        window.edit_context = None
        window.conversation = SimpleNamespace(
            current_chat=chat,
            has_reply_context=lambda: False,
            consume_composed_message=Mock(return_value="/transcribe here off"),
        )
        window.status_bar = SimpleNamespace(SetStatusText=Mock())
        window.xmpp = SimpleNamespace(send_reply=Mock(), send_message=Mock())
        window._require_whatsapp_connection = lambda: True
        window._add_pending_outgoing_message = Mock()
        window._mark_current_chat_displayed = Mock()

        MainWindow._on_send_message(window, SimpleNamespace())

        window.xmpp.send_message.assert_called_once_with(
            chat.jid,
            "/transcribe here off",
            is_group=True,
            ephemeral=True,
        )
        window.xmpp.send_reply.assert_not_called()
        window._add_pending_outgoing_message.assert_not_called()
        window._mark_current_chat_displayed.assert_not_called()
        window.status_bar.SetStatusText.assert_called_once_with(
            "Ejecutando comando local..."
        )


class LocalBridgeCommandArchiveFilteringTests(unittest.TestCase):
    def test_outgoing_carbon_or_echo_is_not_emitted(self) -> None:
        stanza = {
            "type": "chat",
            "body": "/status",
        }
        client = SimpleNamespace(
            _message_retraction_from_stanza=lambda *_args, **_kwargs: None,
            _stanza_is_groupchat=lambda _stanza: False,
            _emit=Mock(),
        )

        BridgeXmppClient._emit_message_from_stanza(client, stanza, outgoing=True)

        client._emit.assert_not_called()

    def test_mam_command_is_not_converted_to_a_message(self) -> None:
        result = ET.fromstring(
            """
            <result xmlns="urn:xmpp:mam:2">
              <forwarded xmlns="urn:xmpp:forward:0">
                <message xmlns="jabber:client"
                         from="me@example.test"
                         to="contact@example.test"
                         type="chat">
                  <body>/transcribe here off</body>
                </message>
              </forwarded>
            </result>
            """
        )
        client = SimpleNamespace(
            _forwarded_message_from_xml=BridgeXmppClient._forwarded_message_from_xml,
            _message_retraction_from_xml=lambda *_args, **_kwargs: None,
            _media_from_xml=lambda _message: ("", "", "", "", 0, None),
            _xml_message_addresses_groupchat=lambda _message: False,
            _sender_jid_from_message_xml=lambda *_args, **_kwargs: "me@example.test",
            _sender_name_from_message_xml=lambda *_args, **_kwargs: "",
            _message_xml_is_outgoing=lambda *_args, **_kwargs: True,
        )

        message = BridgeXmppClient._message_from_forwarded_xml(
            client,
            "contact@example.test",
            result,
        )

        self.assertIsNone(message)

    def test_outgoing_command_is_not_used_as_inbox_preview(self) -> None:
        xml = ET.fromstring(
            """
            <message xmlns="jabber:client">
              <entry xmlns="urn:xmpp:inbox:1"
                     jid="contact@example.test"
                     unread="0">
                <result xmlns="urn:xmpp:mam:2">
                  <forwarded xmlns="urn:xmpp:forward:0">
                    <message xmlns="jabber:client"
                             from="me@example.test"
                             to="contact@example.test"
                             type="chat">
                      <body>/stats</body>
                    </message>
                  </forwarded>
                </result>
              </entry>
            </message>
            """
        )
        client = SimpleNamespace(
            _int_or_zero=BridgeXmppClient._int_or_zero,
            _forwarded_message_from_xml=BridgeXmppClient._forwarded_message_from_xml,
            _xml_message_addresses_groupchat=lambda _message: False,
            _message_xml_is_outgoing=lambda *_args, **_kwargs: True,
            _body_from_message_xml=BridgeXmppClient._body_from_message_xml,
        )

        entry = BridgeXmppClient._inbox_entry_from_stanza(
            client,
            SimpleNamespace(xml=xml),
        )

        self.assertIsNone(entry)

    def test_recent_activity_identifies_archived_outgoing_commands(self) -> None:
        stanza = {
            "body": "/stats detail",
        }
        result = {"mam_result": {"forwarded": {"stanza": stanza}}}
        client = SimpleNamespace(
            _stanza_is_groupchat=lambda _stanza: False,
            _sender_jid_from_stanza=lambda *_args, **_kwargs: "me@example.test",
            _message_is_outgoing=lambda *_args, **_kwargs: True,
        )

        self.assertTrue(
            BridgeXmppClient._mam_result_is_outgoing_local_bridge_command(
                client,
                result,
            )
        )


if __name__ == "__main__":
    unittest.main()
