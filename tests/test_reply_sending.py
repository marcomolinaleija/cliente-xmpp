from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.ui.main_window import MainWindow


class ReplySendingTests(unittest.TestCase):
    @staticmethod
    def _status_bar() -> SimpleNamespace:
        return SimpleNamespace(SetStatusText=Mock())

    def test_existing_draft_keeps_reply_target_during_composer_clear(self) -> None:
        chat = Chat(jid="contact@example.test", name="Contacto")
        target = Message(
            chat_jid=chat.jid,
            sender_jid=chat.jid,
            body="mensaje original",
            message_id="whatsapp-message-id",
        )
        window = MainWindow.__new__(MainWindow)
        pending_messages: list[Message] = []
        reply_visible = True

        def consume_composed_message() -> str:
            window.reply_context = None
            return "borrador que ya estaba escrito"

        def clear_reply_quote() -> None:
            nonlocal reply_visible
            reply_visible = False

        window.reply_context = target
        window.edit_context = None
        window.current_jid = "me@example.test"
        window.conversation = SimpleNamespace(
            current_chat=chat,
            has_reply_context=lambda: reply_visible,
            consume_composed_message=consume_composed_message,
            clear_reply_quote=clear_reply_quote,
            focus_composer=Mock(),
        )
        window.status_bar = self._status_bar()
        window.xmpp = SimpleNamespace(send_reply=Mock(), send_message=Mock())
        window._require_whatsapp_connection = lambda: True
        window._mention_references_for_message = lambda _chat, _body: []
        window._add_pending_outgoing_message = pending_messages.append
        window._mark_current_chat_displayed = lambda _jid: None

        MainWindow._on_send_message(window, SimpleNamespace())

        window.xmpp.send_message.assert_not_called()
        window.xmpp.send_reply.assert_called_once()
        reply_args = window.xmpp.send_reply.call_args.args
        self.assertEqual(reply_args[:4], (
            chat.jid,
            "borrador que ya estaba escrito",
            target.sender_jid,
            target.message_id,
        ))
        self.assertEqual(pending_messages[0].reply_to_id, target.message_id)
        self.assertEqual(pending_messages[0].reply_quote, target.body)
        self.assertFalse(reply_visible)

    def test_pending_local_message_cannot_be_selected_as_reply_target(self) -> None:
        chat = Chat(jid="contact@example.test", name="Contacto")
        target = Message(
            chat_jid=chat.jid,
            sender_jid="me",
            body="todavía enviándose",
            outgoing=True,
            message_id="cliente-xmpp-temporary-id",
            delivery_state="pending",
        )
        window = MainWindow.__new__(MainWindow)
        window.reply_context = None
        window.edit_context = None
        window.conversation = SimpleNamespace(
            current_chat=chat,
            insert_reply_quote=Mock(),
        )
        window.status_bar = self._status_bar()
        window._require_whatsapp_connection = lambda: True

        MainWindow._reply_to_message(window, target)

        self.assertIsNone(window.reply_context)
        window.conversation.insert_reply_quote.assert_not_called()
        status = window.status_bar.SetStatusText.call_args.args[0]
        self.assertIn("todavía se está enviando", status)

    def test_visible_reply_without_target_does_not_send_plain_message(self) -> None:
        chat = Chat(jid="contact@example.test", name="Contacto")
        window = MainWindow.__new__(MainWindow)
        window.reply_context = None
        window.edit_context = None
        window.conversation = SimpleNamespace(
            current_chat=chat,
            has_reply_context=lambda: True,
            consume_composed_message=Mock(return_value="no debe salir"),
            clear_reply_quote=Mock(),
            focus_composer=Mock(),
        )
        window.status_bar = self._status_bar()
        window.xmpp = SimpleNamespace(send_reply=Mock(), send_message=Mock())
        window._require_whatsapp_connection = lambda: True

        MainWindow._on_send_message(window, SimpleNamespace())

        window.conversation.consume_composed_message.assert_not_called()
        window.xmpp.send_reply.assert_not_called()
        window.xmpp.send_message.assert_not_called()
        window.conversation.clear_reply_quote.assert_called_once()


if __name__ == "__main__":
    unittest.main()
