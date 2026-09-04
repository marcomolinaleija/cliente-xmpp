from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.xmpp.client import XmppService
from cliente_xmpp.xmpp.events import FileBatchCompleted, MessageReceived, XmppError


class ClipboardBatchTests(unittest.TestCase):
    def test_xmpp_batch_uploads_serially_and_reports_partial_failures(self) -> None:
        order: list[str] = []

        class Client:
            async def send_file(self, _to_jid: str, path: str, **_kwargs: object) -> Message:
                order.append(path)
                if path == "second.pdf":
                    raise OSError("fallo de prueba")
                return Message(
                    chat_jid="chat@example.test",
                    sender_jid="me@example.test",
                    body=path,
                    outgoing=True,
                )

        class Loop:
            def call_soon_threadsafe(self, callback: object) -> None:
                callback()

            @staticmethod
            def create_task(coro: object) -> None:
                asyncio.run(coro)

        events: list[object] = []
        service = XmppService(events.append)
        service._client = Client()
        service._loop = Loop()

        service.send_files_serial(
            "chat@example.test",
            ["first.jpg", "second.pdf", "third.png"],
        )

        self.assertEqual(order, ["first.jpg", "second.pdf", "third.png"])
        self.assertEqual(
            [event.message.body for event in events if isinstance(event, MessageReceived)],
            ["first.jpg", "third.png"],
        )
        self.assertEqual(
            [event for event in events if isinstance(event, FileBatchCompleted)],
            [FileBatchCompleted("chat@example.test", 3, 2, 1)],
        )
        self.assertFalse(any(isinstance(event, XmppError) for event in events))

    def test_multiple_files_are_passed_as_one_stable_serial_snapshot(self) -> None:
        chat = Chat(jid="contact@example.test", name="Contacto")
        window = MainWindow.__new__(MainWindow)
        first = Path("first.jpg")
        second = Path("second.pdf")
        window.conversation = SimpleNamespace(
            has_reply_context=lambda: False,
            clear_reply_quote=Mock(),
            focus_composer=Mock(),
        )
        window.reply_context = None
        window.current_jid = "me@example.test"
        window.status_bar = SimpleNamespace(SetStatusText=Mock())
        window.speaker = SimpleNamespace(speak=Mock())
        window.xmpp = SimpleNamespace(send_files_serial=Mock())
        window._require_whatsapp_connection = lambda: True
        window._mark_current_chat_displayed = Mock()

        with patch.object(Path, "is_file", return_value=True):
            MainWindow._send_files_to_chat(window, chat, [first, second])

        window.xmpp.send_files_serial.assert_called_once_with(
            chat.jid,
            [str(first), str(second)],
            is_group=False,
            reply_to_jid="",
            reply_to_id="",
            reply_quote="",
        )
        window.speaker.speak.assert_called_once_with("Enviando 2 archivos...")

    def test_file_batch_result_announces_total_and_partial_failures(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.status_bar = SimpleNamespace(SetStatusText=Mock())
        window.speaker = SimpleNamespace(speak=Mock())

        MainWindow._handle_file_batch_completed(window, "chat@example.test", 4, 3, 1)

        message = "Archivos enviados: 3 de 4; fallos: 1"
        window.status_bar.SetStatusText.assert_called_once_with(message)
        window.speaker.speak.assert_called_once_with(message)


if __name__ == "__main__":
    unittest.main()
