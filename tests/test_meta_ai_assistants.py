from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx

from cliente_xmpp.models.assistants import (
    MAX_ASSISTANT_SUGGESTIONS,
    AssistantApproval,
    approval_command,
    assistant_approval,
    assistant_suggestions,
    is_meta_ai_assistant_jid,
)
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.models.local_commands import is_local_bridge_command
from cliente_xmpp.ui.chat_list_panel import ChatListPanel
from cliente_xmpp.ui.conversation_panel import ConversationPanel

COMPONENT = "whatsapp.example.invalid"
MUSE = f"+bot.1807055946647697@{COMPONENT}"
PERSON = f"+12025550100@{COMPONENT}"
ANSWER = (
    "Respuesta de prueba\n\nEnlaces:\n1. Fuente ficticia: https://example.invalid/a"
    "\n\nSugerencias:\n• ¿Algo más?\n• Dame un ejemplo & otro"
)


def answer(body: str = ANSWER, chat: str = MUSE, **kwargs: object) -> Message:
    return Message(chat_jid=chat, sender_jid=chat, body=body, **kwargs)


class MetaAiAssistantTests(unittest.TestCase):
    def test_only_reserved_bot_aliases_are_assistants(self) -> None:
        cases = {
            f"+13135550002@{COMPONENT}": True,
            f"+13135550002@{COMPONENT}/resource": True,
            f"+13165550099@{COMPONENT}": True,
            MUSE: True,
            f"+bot.@{COMPONENT}": False,
            f"+bot.abc@{COMPONENT}": False,
            PERSON: False,
            f"+131355500021@{COMPONENT}": False,
            f"13135550002@{COMPONENT}": False,
            "+13135550002": False,
            f"#120363000000000000@{COMPONENT}": False,
            "": False,
        }
        for jid, expected in cases.items():
            with self.subTest(jid=jid):
                self.assertIs(is_meta_ai_assistant_jid(jid), expected)

    def test_chat_row_names_the_assistant_without_hiding_unread_state(self) -> None:
        panel = SimpleNamespace(_pinned_chat_jids=set())
        assistant = Chat(jid=f"+13135550002@{COMPONENT}", name="Meta AI", unread_count=2)
        self.assertEqual(
            ChatListPanel._format_status(panel, assistant),
            "asistente de Meta, 2 mensajes no leídos",
        )
        person = Chat(jid=PERSON, name="Fixture contact")
        self.assertEqual(ChatListPanel._format_status(panel, person), "")

    def test_assistants_stay_visible_without_activity(self) -> None:
        from cliente_xmpp.ui.main_window import MainWindow

        muse = Chat(jid=MUSE, name="Muse")
        meta_ai = Chat(jid=f"+13135550002@{COMPONENT}", name="Meta AI")
        empty_contact = Chat(jid=PERSON, name="Fixture contact")
        visible = MainWindow._chats_with_activity([empty_contact, muse, meta_ai])
        self.assertEqual([chat.jid for chat in visible], [muse.jid, meta_ai.jid])

    def test_suggestions_come_only_from_received_assistant_answers(self) -> None:
        self.assertEqual(assistant_suggestions(answer()), ("¿Algo más?", "Dame un ejemplo & otro"))
        many = "Texto\n\nSugerencias:\n" + "\n".join(f"• Opción {i}" for i in range(12))
        self.assertEqual(len(assistant_suggestions(answer(many))), MAX_ASSISTANT_SUGGESTIONS)
        for message in (
            answer(chat=PERSON),  # a person writing the same text is not a bot offer
            answer(outgoing=True),
            answer(retracted=True),
            answer("Sin sugerencias"),
            answer("Texto\n\nSugerencias:\n• Una\nTexto añadido después"),
            None,
        ):
            with self.subTest(message=message and message.body[-20:]):
                self.assertEqual(assistant_suggestions(message), ())


APPROVAL = (
    "Solicitud de aprobación de Muse: Programa de prueba\n"
    "¿Permitir que el agente ejecute este programa?\n\n"
    "Detalles:\n- Ruta: /usr/bin/fixture-tool\n\n"
    "Opciones:\n1. Permitir una vez\n2. Denegar\n"
    "Estado: pendiente\n\n"
    "Puedes responder con los botones de este mensaje (Tab desde el historial) o en el teléfono."
)
APPROVAL_ID = "can-muse-approval-fixture-approval-1"


def approval_answer(body: str = APPROVAL, **kwargs: object) -> Message:
    kwargs.setdefault("message_id", APPROVAL_ID)
    return answer(body, **kwargs)


class AssistantApprovalTests(unittest.TestCase):
    def test_only_pending_well_formed_muse_approvals_are_actionable(self) -> None:
        approval = assistant_approval(approval_answer())
        self.assertEqual(approval, AssistantApproval(
            reference="fixture-approval-1",
            title="Solicitud de aprobación de Muse: Programa de prueba",
            options=("Permitir una vez", "Denegar"),
        ))
        self.assertEqual(approval_command(approval, 1), "/muse-decidir 2 fixture-approval-1")
        with self.assertRaises(ValueError):
            approval_command(approval, 2)
        self.assertTrue(is_local_bridge_command(approval_command(approval, 0)))
        for body, kwargs in (
            (APPROVAL, {"chat": PERSON}),  # a person cannot impersonate an approval
            (APPROVAL, {"outgoing": True}),
            (APPROVAL, {"retracted": True}),
            (APPROVAL, {"message_id": "otro-mensaje"}),  # only bridge approvals
            (APPROVAL, {"message_id": "can-muse-approval-"}),
            (APPROVAL, {"message_id": "can-muse-approval-con espacio"}),
            (APPROVAL.replace("Estado: pendiente", "Estado: aprobada"), {}),
            (APPROVAL.replace("Estado: pendiente", "Decidido: Denegar"), {}),
            (APPROVAL.replace("2. Denegar", "3. Denegar"), {}),  # broken numbering
            ("Hola\n" + APPROVAL, {}),
        ):
            with self.subTest(body=body[-40:], **kwargs):
                self.assertIsNone(assistant_approval(approval_answer(body, **kwargs)))

    def test_decision_needs_explicit_confirmation(self) -> None:
        from cliente_xmpp.ui.main_window import MainWindow

        chat = Chat(MUSE, "Muse")
        source = approval_answer()
        approval = assistant_approval(source)
        window = MainWindow.__new__(MainWindow)
        window.conversation = SimpleNamespace(current_chat=chat)
        window.status_bar = Mock()
        window.xmpp = Mock()
        window._require_whatsapp_connection = Mock(return_value=True)
        asked: list[str] = []
        window._ask_assistant_approval = lambda text: asked.append(text) or False
        MainWindow._confirm_assistant_approval(window, source, approval, 1)
        window.xmpp.send_message.assert_not_called()
        self.assertIn("«Denegar»", asked[0])
        self.assertNotIn("fixture-approval-1", asked[0])  # internal IDs stay hidden

        window._ask_assistant_approval = lambda _text: True
        MainWindow._confirm_assistant_approval(window, source, approval, 1)
        window.xmpp.send_message.assert_called_once_with(
            MUSE, "/muse-decidir 2 fixture-approval-1", is_group=False, ephemeral=True
        )
        # Another chat or an out-of-range option never sends.
        window.xmpp.reset_mock()
        window.conversation.current_chat = Chat(PERSON, "Fixture contact")
        MainWindow._confirm_assistant_approval(window, source, approval, 0)
        window.conversation.current_chat = chat
        MainWindow._confirm_assistant_approval(window, source, approval, 5)
        window.xmpp.send_message.assert_not_called()


class AssistantSuggestionNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = wx.App.Get() or wx.App(False)

    def test_tab_from_focused_answer_reaches_suggestion_buttons(self) -> None:
        frame = wx.Frame(None)
        sent: list[tuple[Message, str]] = []
        with patch("cliente_xmpp.ui.conversation_panel.MpvAudioPlayer"), \
                patch("cliente_xmpp.ui.conversation_panel.NvdaSpeaker"):
            panel = ConversationPanel(
                frame, lambda jid: jid,
                on_assistant_suggestion=lambda message, prompt: sent.append((message, prompt)),
            )
        try:
            panel.set_chat(Chat(MUSE, "Muse"))
            offer = answer()
            plain = answer("Respuesta sin sugerencias")
            panel.set_messages([offer, plain])
            panel.focus_message(offer)
            first, second, *rest = panel.suggestion_buttons
            self.assertTrue(first.IsShown() and second.IsShown())
            self.assertFalse(any(button.IsShown() for button in rest))
            self.assertEqual(first.GetName(), "Sugerencia 1 de 2: ¿Algo más?")
            # "&" stays literal instead of becoming a keyboard mnemonic.
            self.assertEqual(second.GetLabelText(), "Dame un ejemplo & otro")

            # Tab order: the history, then the suggestions, before the composer.
            children = list(panel.GetChildren())
            self.assertLess(children.index(panel.messages), children.index(first))
            self.assertLess(children.index(first), children.index(panel.compose))
            frame.Show()
            panel.messages.SetFocus()
            wx.Yield()
            if wx.Window.FindFocus() is panel.messages:
                panel.messages.Navigate()
                wx.Yield()
                self.assertIs(wx.Window.FindFocus(), first)

            panel.compose.SetValue("Borrador intacto")
            second.ProcessEvent(wx.CommandEvent(wx.wxEVT_BUTTON, second.GetId()))
            self.assertEqual(sent, [(offer, "Dame un ejemplo & otro")])
            self.assertEqual(panel.compose.GetValue(), "Borrador intacto")

            # Moving to a message without offers hides the buttons and keeps focus usable.
            first.SetFocus()
            panel.focus_message(plain)
            self.assertFalse(any(button.IsShown() for button in panel.suggestion_buttons))
            self.assertIsNot(wx.Window.FindFocus(), first)
            # A stale button press after the offer disappeared sends nothing.
            panel._on_assistant_suggestion(0)
            self.assertEqual(len(sent), 1)
        finally:
            wx.Yield()
            frame.Destroy()
            wx.Yield()

    def test_pending_approval_offers_option_buttons_before_suggestions(self) -> None:
        frame = wx.Frame(None)
        chosen: list[tuple[str, int]] = []
        with patch("cliente_xmpp.ui.conversation_panel.MpvAudioPlayer"), \
                patch("cliente_xmpp.ui.conversation_panel.NvdaSpeaker"):
            panel = ConversationPanel(
                frame, lambda jid: jid,
                on_assistant_approval=lambda message, approval, index: chosen.append(
                    (approval.reference, index)
                ),
            )
        try:
            panel.set_chat(Chat(MUSE, "Muse"))
            pending = approval_answer()
            decided = approval_answer(APPROVAL.replace("Estado: pendiente", "Estado: aprobada"),
                                      message_id="can-muse-approval-fixture-approval-2")
            panel.set_messages([pending, decided])
            panel.focus_message(pending)
            allow, deny, *rest = panel.approval_buttons
            self.assertTrue(allow.IsShown() and deny.IsShown())
            self.assertFalse(any(button.IsShown() for button in rest))
            self.assertEqual(allow.GetName(), "Aprobación de Muse, opción 1 de 2: Permitir una vez")
            children = list(panel.GetChildren())
            self.assertLess(children.index(panel.messages), children.index(allow))
            self.assertLess(children.index(allow), children.index(panel.suggestion_buttons[0]))
            deny.ProcessEvent(wx.CommandEvent(wx.wxEVT_BUTTON, deny.GetId()))
            self.assertEqual(chosen, [("fixture-approval-1", 1)])
            # Once decided, the options disappear and a stale press does nothing.
            panel.focus_message(decided)
            self.assertFalse(any(button.IsShown() for button in panel.approval_buttons))
            panel._on_assistant_approval(0)
            self.assertEqual(len(chosen), 1)
        finally:
            wx.Yield()
            frame.Destroy()
            wx.Yield()

    def test_people_never_get_suggestion_buttons(self) -> None:
        frame = wx.Frame(None)
        with patch("cliente_xmpp.ui.conversation_panel.MpvAudioPlayer"), \
                patch("cliente_xmpp.ui.conversation_panel.NvdaSpeaker"):
            panel = ConversationPanel(
                frame, lambda jid: jid, on_assistant_suggestion=lambda *_: None
            )
        try:
            panel.set_chat(Chat(PERSON, "Fixture contact"))
            message = answer(chat=PERSON)
            panel.set_messages([message])
            panel.focus_message(message)
            self.assertFalse(any(button.IsShown() for button in panel.suggestion_buttons))
        finally:
            wx.Yield()
            frame.Destroy()
            wx.Yield()


if __name__ == "__main__":
    unittest.main()
