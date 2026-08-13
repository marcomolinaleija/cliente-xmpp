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

    def test_group_reply_targets_room_occupant_instead_of_private_contact(self) -> None:
        chat = Chat(
            jid="#120363216552048055@whatsapp.example.test",
            name="Grupo",
            is_group=True,
        )
        target = Message(
            chat_jid=chat.jid,
            sender_jid="+5214493860911@whatsapp.example.test",
            sender_name="Marquiños",
            body="mensaje original",
            message_id="whatsapp-message-id",
            chat_is_group=True,
        )

        reply_to = MainWindow._reply_target_jid(
            chat,
            target,
            "angel@example.test",
        )

        self.assertEqual(reply_to, f"{chat.jid}/Marquiños")

    def test_group_reply_to_own_message_uses_own_room_nick(self) -> None:
        chat = Chat(
            jid="#120363216552048055@whatsapp.example.test",
            name="Grupo",
            is_group=True,
        )
        target = Message(
            chat_jid=chat.jid,
            sender_jid="me",
            sender_name="Tú",
            body="mensaje propio",
            outgoing=True,
            message_id="whatsapp-message-id",
            chat_is_group=True,
        )

        reply_to = MainWindow._reply_target_jid(
            chat,
            target,
            "angel@example.test",
        )

        self.assertEqual(reply_to, f"{chat.jid}/angel")

    def test_private_reply_quote_navigates_back_to_the_group_message(self) -> None:
        group = Chat(
            jid="#120363216552048055@whatsapp.example.test",
            name="Grupo",
            is_group=True,
        )
        private_chat = Chat(jid="+524491234567@whatsapp.example.test", name="Marquiños")
        source = Message(
            chat_jid=group.jid,
            sender_jid="+524491234567@whatsapp.example.test",
            body="Mensaje del grupo",
            message_id="group-message-id",
            chat_is_group=True,
        )
        reply = Message(
            chat_jid=private_chat.jid,
            sender_jid="me",
            body="Respuesta privada",
            outgoing=True,
            reply_to_jid=f"{group.jid}/Marquiños",
            reply_to_id=source.message_id,
        )
        window = MainWindow.__new__(MainWindow)
        window.conversation = SimpleNamespace(
            current_chat=private_chat,
            find_message_by_id=lambda _message_id: None,
        )
        window.messages_by_chat = {group.jid: [source]}
        window._chat_by_jid = lambda chat_jid: group if chat_jid == group.jid else None

        target = MainWindow._quoted_message_navigation_target(window, reply)

        self.assertEqual(target, (group, source))

    def test_private_reply_sends_to_contact_and_keeps_group_reply_target(self) -> None:
        private_chat = Chat(jid="+524491234567@whatsapp.example.test", name="Rabanita")
        reply_context = Message(
            chat_jid=private_chat.jid,
            sender_jid="#group@whatsapp.example.test/Rabanita",
            body="Mensaje del grupo",
            message_id="group-message-id",
        )
        window = MainWindow.__new__(MainWindow)
        pending_messages: list[Message] = []
        window.reply_context = reply_context
        window.edit_context = None
        window.current_jid = "me@example.test"
        window.conversation = SimpleNamespace(
            current_chat=private_chat,
            has_reply_context=lambda: True,
            consume_composed_message=lambda: "Respuesta privada",
            clear_reply_quote=Mock(),
            focus_composer=Mock(),
        )
        window.status_bar = self._status_bar()
        window.xmpp = SimpleNamespace(send_reply=Mock(), send_message=Mock())
        window._require_whatsapp_connection = lambda: True
        window._mention_references_for_message = lambda _chat, _body: []
        window._add_pending_outgoing_message = pending_messages.append
        window._mark_current_chat_displayed = lambda _jid: None

        MainWindow._on_send_message(window, SimpleNamespace())

        window.xmpp.send_reply.assert_called_once()
        reply_args = window.xmpp.send_reply.call_args.args
        self.assertEqual(reply_args[:4], (
            private_chat.jid,
            "Respuesta privada",
            reply_context.sender_jid,
            reply_context.message_id,
        ))
        self.assertEqual(pending_messages[0].chat_jid, private_chat.jid)
        self.assertEqual(pending_messages[0].reply_to_jid, reply_context.sender_jid)
        self.assertEqual(pending_messages[0].reply_to_id, reply_context.message_id)

    def test_private_reply_can_load_group_quote_after_restart(self) -> None:
        group = Chat(
            jid="#120363216552048055@whatsapp.example.test",
            name="Grupo",
            is_group=True,
        )
        private_chat = Chat(jid="+524491234567@whatsapp.example.test", name="Rabanita")
        reply = Message(
            chat_jid=private_chat.jid,
            sender_jid="me",
            body="Respuesta privada",
            outgoing=True,
            reply_to_jid=f"{group.jid}/Rabanita",
            reply_to_id="group-message-id",
        )
        window = MainWindow.__new__(MainWindow)
        window.conversation = SimpleNamespace(
            current_chat=private_chat,
            find_message_by_id=lambda _message_id: None,
        )
        window.messages_by_chat = {group.jid: []}
        window._chat_by_jid = lambda chat_jid: group if chat_jid == group.jid else None
        window._load_quoted_group_message_async = Mock()
        window.status_bar = self._status_bar()

        self.assertTrue(MainWindow._can_go_to_quoted_message(window, reply))
        MainWindow._go_to_quoted_message(window, reply)

        window._load_quoted_group_message_async.assert_called_once_with(
            group,
            reply.reply_to_id,
        )

    def test_group_reply_without_occupant_or_nick_is_rejected(self) -> None:
        chat = Chat(
            jid="#120363216552048055@whatsapp.example.test",
            name="Grupo",
            is_group=True,
        )
        target = Message(
            chat_jid=chat.jid,
            sender_jid="+5214493860911@whatsapp.example.test",
            body="mensaje sin nick",
            message_id="whatsapp-message-id",
            chat_is_group=True,
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
        self.assertIn("identificar al participante", status)

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

    def test_confirmed_own_message_can_be_selected_as_reply_target(self) -> None:
        chat = Chat(jid="contact@example.test", name="Contacto")
        target = Message(
            chat_jid=chat.jid,
            sender_jid="me",
            body="mensaje propio confirmado",
            outgoing=True,
            message_id="cliente-xmpp-confirmed-id",
            delivery_state="sent",
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

        self.assertIs(window.reply_context, target)
        window.conversation.insert_reply_quote.assert_called_once_with(target)
        window.status_bar.SetStatusText.assert_called_once_with("Respuesta preparada")

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
