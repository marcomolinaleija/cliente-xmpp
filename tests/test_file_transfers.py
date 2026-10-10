from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx
from aiohttp import web
from slixmpp.plugins.xep_0363.http_upload import HTTPError

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.ui.chat_list_panel import ChatListItem, ChatListPanel
from cliente_xmpp.ui.conversation_panel import ConversationPanel
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.xmpp.client import XmppService, _format_file_send_error
from cliente_xmpp.xmpp.events import FileBatchCompleted, FileTransferUpdated, MessageReceived
from cliente_xmpp.xmpp.http_upload import UploadBudget, upload_file_with_system_resolver


class UploadBudgetTests(unittest.TestCase):
    def test_two_minute_upload_gets_margin_and_extends_when_rate_drops(self):
        budget = UploadBudget(120 * 1024**2, 0)
        budget.advance(60 * 1024**2, 60)
        self.assertEqual(budget.deadline(60), 190)
        earlier = budget.deadline(60)
        budget.advance(1 * 1024**2, 120)
        self.assertGreater(budget.deadline(120), earlier)

    def test_large_upload_is_not_given_a_fixed_sixty_second_deadline(self):
        budget = UploadBudget(91 * 1024**2, 100)
        self.assertGreater(budget.deadline(100), 100 + 3 * 60)
        budget.advance(91 * 1024**2, 220)
        self.assertEqual(budget.deadline(220), 260)

    def test_errors_do_not_expose_signed_urls_or_response_bodies(self):
        self.assertEqual(
            _format_file_send_error(HTTPError(413, "secret https://example.test/token")),
            "El servidor rechazó la subida (HTTP 413).",
        )
        self.assertIn("Tiempo agotado", _format_file_send_error(TimeoutError()))


class HttpStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_http_streams_91_mib_and_reports_progress_without_iq_put_timeout(self):
        size = 91 * 1024**2
        received = 0
        progress = []

        async def accept(request):
            nonlocal received
            async for chunk in request.content.iter_chunked(256 * 1024):
                received += len(chunk)
            return web.Response(status=201)

        app = web.Application(client_max_size=200 * 1024**2)
        app.router.add_put("/fixture", accept)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}/fixture"

        async def request_slot(_service, _filename, actual_size, _mime, *, timeout):
            self.assertEqual(timeout, 60)  # Slot IQ remains bounded, PUT does not.
            self.assertEqual(actual_size, size)
            return {"http_upload_slot": {"put": {"url": url, "headers": []},
                                         "get": {"url": url}}}

        upload = SimpleNamespace(
            max_file_size=200 * 1024**2, upload_service="upload.example.test",
            default_content_type="application/octet-stream", request_slot=request_slot,
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "fixture.apk"
                with path.open("wb") as stream:
                    stream.truncate(size)
                result = await upload_file_with_system_resolver(
                    upload, path, size=size, content_type="application/octet-stream", timeout=60,
                    progress=lambda sent, total: progress.append((sent, total)),
                )
            self.assertEqual(result, url)
            self.assertEqual(received, size)
            self.assertEqual(progress[0], (0, size))
            self.assertEqual(progress[-1], (size, size))
            self.assertTrue(any(0 < sent < size for sent, _ in progress))
        finally:
            await runner.cleanup()

    async def test_stalled_writer_times_out_and_closes_source_without_sending(self):
        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def put(self, _url, *, data, headers):
                self.payload = data
                await asyncio.sleep(1)

        session = Session()

        async def slot(*_args, **_kwargs):
            return {"http_upload_slot": {
                "put": {"url": "https://example.test/fixture", "headers": []},
            }}

        upload = SimpleNamespace(max_file_size=100, request_slot=slot,
                                 upload_service="upload.example.test")
        progress = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.apk"
            path.write_bytes(b"fixture")
            with (
                patch("cliente_xmpp.xmpp.http_upload.ClientSession", return_value=session),
                patch("cliente_xmpp.xmpp.http_upload.UPLOAD_IDLE_SECONDS", 0.03),
            ):
                with self.assertRaises(TimeoutError):
                    await upload_file_with_system_resolver(
                        upload, path, size=7, content_type="application/octet-stream", timeout=60,
                        progress=lambda sent, total: progress.append(sent),
                    )
            self.assertTrue(session.payload._value.closed)
            self.assertEqual(progress, [0])


class TransferServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "fixture.apk"
        self.path.write_bytes(b"fixture")
        self.events = []
        self.service = XmppService(self.events.append)
        self.service._loop = asyncio.get_running_loop()
        self.calls = []
        self.fail = True

        async def send_file(chat_jid, path, **options):
            self.calls.append((chat_jid, path, options))
            options["upload_progress"](3, 7)
            if self.fail:
                raise TimeoutError()
            options["before_submit"]()
            return Message(chat_jid, "Yo", "Archivo enviado", outgoing=True,
                           media_url="https://example.test/fixture",
                           message_id=options["message_id"], delivery_state="sent")

        self.client = SimpleNamespace(
            send_file=send_file,
            settings=SimpleNamespace(jid="me@example.test", host="example.test", port=5222),
        )
        self.service._client = self.client

    async def asyncTearDown(self):
        self.service._cancel_file_transfers()
        await asyncio.gather(*self.service._file_transfer_tasks, return_exceptions=True)
        self.directory.cleanup()

    async def settle(self):
        # Allow thread-safe scheduling to register its task first.
        await asyncio.sleep(0)
        await asyncio.gather(*tuple(self.service._file_transfer_tasks))

    def transfer_id(self):
        return next(event.transfer_id for event in self.events
                    if isinstance(event, FileTransferUpdated))

    def queue(self):
        self.service.send_files_serial(
            "room@example.test", [str(self.path)], is_group=True,
            reply_to_jid="room@example.test/participant", reply_to_id="remote-fixture",
            reply_quote="Fixture quote",
        )

    async def test_retry_keeps_identity_and_reply_without_double_send(self):
        self.queue()
        await self.settle()
        identity = self.transfer_id()
        failed = [event for event in self.events if isinstance(event, FileTransferUpdated)
                  and event.state == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("Tiempo agotado", failed[0].detail)
        self.assertTrue(any(isinstance(event, FileTransferUpdated) and event.percent == 42
                            for event in self.events))
        self.assertFalse(any(isinstance(event, MessageReceived) for event in self.events))
        self.fail = False
        self.assertTrue(self.service.retry_file_transfer(identity))
        self.assertFalse(self.service.retry_file_transfer(identity))
        await self.settle()
        self.assertEqual(len(self.calls), 2)
        for chat, path, options in self.calls:
            self.assertEqual(chat, "room@example.test")
            self.assertEqual(path, str(self.path))
            self.assertEqual(options["message_id"], identity)
            self.assertTrue(options["is_group"])
            self.assertEqual(options["reply_to_jid"], "room@example.test/participant")
            self.assertEqual(options["reply_to_id"], "remote-fixture")
        sent = [event for event in self.events if isinstance(event, MessageReceived)]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].message.message_id, identity)
        self.assertFalse(self.service.retry_file_transfer(identity))
        self.assertEqual(self.path.read_bytes(), b"fixture")

    async def test_retry_rejects_changed_profile_and_file(self):
        self.queue()
        await self.settle()
        identity = self.transfer_id()
        self.client.settings.host = "other.example.test"
        self.assertFalse(self.service.retry_file_transfer(identity))
        self.client.settings.host = "example.test"
        self.path.write_bytes(b"changed fixture")
        self.assertTrue(self.service.retry_file_transfer(identity))
        await self.settle()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("archivo cambió", self.service._file_transfers[identity].detail)

    async def test_no_retry_for_uncertain_stanza_submission(self):
        async def fail_after_submission(_chat, _path, **options):
            options["before_submit"]()
            raise OSError("write failed")

        self.client.send_file = fail_after_submission
        self.queue()
        await self.settle()
        identity = self.transfer_id()
        self.assertEqual(self.service._file_transfers[identity].state, "uncertain")
        self.assertFalse(self.service.retry_file_transfer(identity))

    async def test_failed_row_stays_owned_when_connection_has_closed(self):
        self.queue()
        await self.settle()
        self.service._client = None
        self.assertTrue(self.service.file_transfer_is_current(self.transfer_id()))
        self.assertFalse(self.service.retry_file_transfer(self.transfer_id()))

    async def test_file_change_during_upload_blocks_submission(self):
        async def modify_during_upload(_chat, _path, **options):
            self.path.write_bytes(b"different fixture")
            options["before_submit"]()

        self.client.send_file = modify_during_upload
        self.queue()
        await self.settle()
        transfer = self.service._file_transfers[self.transfer_id()]
        self.assertFalse(transfer.submitted)
        self.assertEqual(transfer.state, "failed")
        self.assertFalse(any(isinstance(event, MessageReceived) for event in self.events))

    async def test_cancellation_marks_active_and_remaining_files_without_uploading_next(self):
        started = asyncio.Event()

        async def stall(_chat, _path, **_options):
            started.set()
            await asyncio.sleep(10)

        self.client.send_file = stall
        self.service.send_files_serial("chat@example.test", [str(self.path), str(self.path)])
        await asyncio.wait_for(started.wait(), 1)
        self.service._cancel_file_transfers()
        await self.settle()
        self.assertEqual([item.state for item in self.service._file_transfers.values()],
                         ["failed", "failed"])
        result = next(event for event in self.events if isinstance(event, FileBatchCompleted))
        self.assertEqual((result.succeeded, result.failed), (0, 2))


class TransferUiTests(unittest.TestCase):
    def window(self):
        window = MainWindow.__new__(MainWindow)
        window.current_jid = "me@example.test"
        window.messages_by_chat = {}
        window.xmpp = SimpleNamespace(file_transfer_is_current=lambda _identity: True,
                                      retry_file_transfer=Mock(return_value=True))
        window.conversation = SimpleNamespace(
            current_chat=Chat("chat@example.test", "Fixture"),
            insert_message_sorted=Mock(), refresh_message=Mock(),
        )
        window.chat_list = SimpleNamespace(set_file_transfer_preview=Mock())
        window.status_bar = SimpleNamespace(SetStatusText=Mock())
        window.speaker = SimpleNamespace(speak=Mock())
        window._require_whatsapp_connection = lambda: True
        return window

    def test_progress_refreshes_one_row_then_success_promotes_without_downgrading_delivery(self):
        window = self.window()
        event = FileTransferUpdated("chat@example.test", "fixture-id", "fixture.apk", "queued")
        window._handle_file_transfer_updated(event)
        message = window.messages_by_chat[event.chat_jid][0]
        event.state, event.percent = "uploading", 40
        window._handle_file_transfer_updated(event)
        self.assertEqual(len(window.messages_by_chat[event.chat_jid]), 1)
        window.conversation.insert_message_sorted.assert_called_once_with(message)
        window.conversation.refresh_message.assert_called_once_with(message)
        self.assertIn("40 %", ConversationPanel._format_delivery_state(message))
        window.speaker.speak.assert_not_called()
        message.delivery_state = "delivered"
        incoming = Message(event.chat_jid, "Yo", "Archivo enviado", outgoing=True,
                           media_url="https://example.test/fixture", message_id=event.transfer_id,
                           delivery_state="sent")
        MainWindow._merge_message_metadata(message, incoming)
        self.assertFalse(message.upload_id)
        self.assertEqual(message.body, incoming.body)
        self.assertEqual(message.delivery_state, "delivered")
        window._refresh_file_transfer_preview(event.chat_jid)
        window.chat_list.set_file_transfer_preview.assert_called_with(event.chat_jid, "")

    def test_failed_row_retries_only_once_and_unsent_is_not_persisted(self):
        window = self.window()
        event = FileTransferUpdated("chat@example.test", "fixture-id", "fixture.apk", "queued")
        window._handle_file_transfer_updated(event)
        event.state, event.detail = "failed", "Fixture error"
        window._handle_file_transfer_updated(event)
        message = window.messages_by_chat[event.chat_jid][0]
        self.assertIn("Archivo no enviado", ConversationPanel._format_delivery_state(message))
        window._queue_storage_write = Mock()
        window._persist_messages([message])
        window._queue_storage_write.assert_not_called()
        self.assertTrue(window._retry_file_transfer(message))
        self.assertFalse(window._retry_file_transfer(message))
        window.xmpp.retry_file_transfer.assert_called_once_with(event.transfer_id)

    def test_chat_preview_update_preserves_stored_summary_order_and_native_selection(self):
        chat = Chat("chat@example.test", "Fixture", last_message_preview="Previous message")
        panel = ChatListPanel.__new__(ChatListPanel)
        panel._items = [ChatListItem(chat)]
        panel._file_transfer_previews = {}
        panel._pinned_chat_jids = set()
        panel.list_box = SimpleNamespace(SetString=Mock())
        panel.set_file_transfer_preview(chat.jid, "Subiendo 40 %: fixture.apk")
        self.assertEqual(chat.last_message_preview, "Previous message")
        self.assertIn("40 %", panel.list_box.SetString.call_args.args[1])
        panel.set_file_transfer_preview(chat.jid, "")
        self.assertIn("Previous message", panel.list_box.SetString.call_args.args[1])

    def test_enter_then_double_click_does_not_submit_twice(self):
        window = self.window()
        event = FileTransferUpdated("chat@example.test", "fixture-id", "fixture.apk", "queued")
        window._handle_file_transfer_updated(event)
        message = window.messages_by_chat[event.chat_jid][0]
        message.upload_state = "failed"
        window.conversation.selected_message = lambda: message
        key = SimpleNamespace(GetKeyCode=lambda: wx.WXK_RETURN, AltDown=lambda: False,
                              ControlDown=lambda: False, ShiftDown=lambda: False, Skip=Mock())
        window._on_messages_key_down(key)
        window._on_file_transfer_activated(SimpleNamespace(Skip=Mock()))
        window.xmpp.retry_file_transfer.assert_called_once_with(event.transfer_id)
        key.Skip.assert_not_called()

    def test_progress_updates_native_row_without_focus_or_scroll_operations(self):
        message = Message("chat@example.test", "Yo", "Fixture", outgoing=True,
                          upload_id="fixture-id", upload_state="uploading", upload_percent=40)
        panel = ConversationPanel.__new__(ConversationPanel)
        panel._message_rows = [message]
        panel._message_row_indexes = {id(message): 0}
        panel._format_message_row_for_list = Mock(return_value="Subiendo 40 %")
        panel._style_message_item = Mock()
        panel._thumbnail_index_for_message = Mock(return_value=-1)
        item = SimpleNamespace(SetImage=Mock())
        # No SetFocus/Select/EnsureVisible/Freeze methods: any unexpected use fails the test.
        panel.messages = SimpleNamespace(SetItem=Mock(), GetItem=Mock(return_value=item))
        panel.refresh_message(message)
        panel.messages.SetItem.assert_any_call(0, 0, "Subiendo 40 %")

    def test_history_merge_promotes_same_row_and_does_not_duplicate_it(self):
        window = self.window()
        event = FileTransferUpdated("chat@example.test", "fixture-id", "fixture.apk", "queued")
        window._handle_file_transfer_updated(event)
        old_row = window.messages_by_chat[event.chat_jid][0]
        incoming = Message(event.chat_jid, "Yo", "Archivo enviado", outgoing=True,
                           media_url="https://example.test/fixture", message_id=event.transfer_id,
                           delivery_state="sent")
        window._merge_messages(event.chat_jid, [incoming, incoming])
        self.assertEqual(window.messages_by_chat[event.chat_jid], [old_row])
        self.assertFalse(old_row.upload_id)
        window.chat_list.set_file_transfer_preview.assert_called_with(event.chat_jid, "")

    def test_same_filename_queued_twice_never_merges_by_placeholder_text(self):
        window = self.window()
        for identity in ("fixture-one", "fixture-two"):
            window._handle_file_transfer_updated(FileTransferUpdated(
                "chat@example.test", identity, "fixture.apk", "queued",
            ))
        window._merge_messages("chat@example.test", [
            Message("chat@example.test", "other@example.test", "Fixture message",
                    message_id="remote-fixture"),
        ])
        self.assertEqual(len(window.messages_by_chat["chat@example.test"]), 3)


if __name__ == "__main__":
    unittest.main()
