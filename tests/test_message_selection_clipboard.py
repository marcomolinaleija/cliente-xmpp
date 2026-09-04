from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx

from cliente_xmpp.media.downloads import delete_local_media_file
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.ui.conversation_panel import ConversationPanel
from cliente_xmpp.ui.main_window import MainWindow


class _KeyEvent:
    def __init__(self, key_code: int, *, control: bool = False) -> None:
        self._key_code = key_code
        self._control = control

    def GetKeyCode(self) -> int:
        return self._key_code

    def GetUnicodeKey(self) -> int:
        return 0

    def ControlDown(self) -> bool:
        return self._control

    def AltDown(self) -> bool:
        return False

    def ShiftDown(self) -> bool:
        return False


class MessageSelectionClipboardTests(unittest.TestCase):
    def test_selection_shortcuts_start_toggle_and_cancel_without_opening_media(self) -> None:
        conversation = SimpleNamespace(
            message_selection_mode=False,
            begin_message_selection=Mock(),
            toggle_focused_message_selection=Mock(),
            cancel_message_selection=Mock(),
        )
        window = SimpleNamespace(
            conversation=conversation,
            status_bar=SimpleNamespace(SetStatusText=Mock()),
        )

        MainWindow._on_messages_key_down(window, _KeyEvent(wx.WXK_SPACE, control=True))
        conversation.message_selection_mode = True
        MainWindow._on_messages_key_down(window, _KeyEvent(wx.WXK_SPACE))
        MainWindow._on_messages_key_down(window, _KeyEvent(wx.WXK_ESCAPE))

        conversation.begin_message_selection.assert_called_once_with()
        conversation.toggle_focused_message_selection.assert_called_once_with()
        conversation.cancel_message_selection.assert_called_once_with()

    def test_char_hook_keeps_space_from_opening_media_in_selection_mode(self) -> None:
        conversation = SimpleNamespace(
            IsShown=lambda: True,
            messages=SimpleNamespace(HasFocus=lambda: True),
            message_selection_mode=True,
            toggle_focused_message_selection=Mock(),
            begin_message_selection=Mock(),
        )
        window = SimpleNamespace(conversation=conversation)

        MainWindow._on_key_down(window, _KeyEvent(wx.WXK_SPACE))

        conversation.toggle_focused_message_selection.assert_called_once_with()
        conversation.begin_message_selection.assert_not_called()

    def test_selection_row_label_exposes_selected_state_to_screen_readers(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="other@example.test",
            body="mensaje",
        )
        panel = ConversationPanel.__new__(ConversationPanel)
        panel._message_selection_mode = True
        panel.messages = SimpleNamespace(
            GetItemState=lambda index, _state: index == 2,
        )
        panel._format_message_row = lambda _message: "Mensaje visible"

        self.assertEqual(
            ConversationPanel._format_message_row_for_list(panel, 2, message),
            "Seleccionado. Mensaje visible",
        )
        self.assertEqual(
            ConversationPanel._format_message_row_for_list(panel, 1, message),
            "No seleccionado. Mensaje visible",
        )

    def test_logical_selection_survives_native_list_collapse(self) -> None:
        first = Message(
            chat_jid="chat@example.test",
            sender_jid="other@example.test",
            body="primero",
            message_id="first",
        )
        second = Message(
            chat_jid="chat@example.test",
            sender_jid="other@example.test",
            body="segundo",
            message_id="second",
        )
        panel = ConversationPanel.__new__(ConversationPanel)
        panel._message_selection_mode = True
        panel._messages = [first, second]
        panel._selected_message_keys = {
            ConversationPanel._message_focus_key(first),
            ConversationPanel._message_focus_key(second),
        }
        panel._message_rows = [first, second]
        panel._format_message_row = lambda message: message.body
        panel.messages = SimpleNamespace(
            GetItemState=lambda index, _state: index == 0,
        )

        self.assertEqual(panel.selected_messages(), [first, second])
        self.assertEqual(
            ConversationPanel._format_message_row_for_list(panel, 1, second),
            "Seleccionado. segundo",
        )

    def test_batch_buttons_use_logical_selection_count(self) -> None:
        messages = [
            Message(
                chat_jid="chat@example.test",
                sender_jid="other@example.test",
                body=f"mensaje {index}",
                message_id=f"message-{index}",
            )
            for index in (1, 2)
        ]

        class Button:
            def __init__(self) -> None:
                self.visible = False
                self.enabled = False
                self.label = ""

            def IsShown(self) -> bool:
                return self.visible

            def Show(self, value: bool) -> None:
                self.visible = value

            def Enable(self, value: bool) -> None:
                self.enabled = value

            def SetLabel(self, value: str) -> None:
                self.label = value

        buttons = [Button(), Button(), Button()]
        panel = ConversationPanel.__new__(ConversationPanel)
        panel._message_selection_mode = True
        panel._messages = messages
        panel._selected_message_keys = {
            ConversationPanel._message_focus_key(message) for message in messages
        }
        panel.go_to_quoted_button = Button()
        panel.vote_in_poll_button = Button()
        (
            panel.forward_selected_button,
            panel.copy_selected_button,
            panel.delete_selected_button,
        ) = buttons
        panel.selected_message = lambda: None
        panel._can_go_to_quoted_message = lambda _message: False
        panel.Layout = Mock()

        ConversationPanel._update_message_action_buttons(panel)

        self.assertTrue(all(button.visible and button.enabled for button in buttons))
        self.assertEqual(
            [button.label for button in buttons],
            ["Reenviar (2)", "Copiar (2)", "Eliminar (2)"],
        )

    def test_starting_selection_announces_state_before_message(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="other@example.test",
            body="mensaje",
            message_id="message-id",
        )

        class MessageList:
            def __init__(self) -> None:
                self.selected = False
                self.labels: dict[int, str] = {}

            def GetItemState(self, _index: int, _state: int) -> bool:
                return self.selected

            def Select(self, _index: int, selected: bool) -> None:
                self.selected = selected

            def SetItem(self, index: int, _column: int, value: str) -> None:
                self.labels[index] = value

        message_list = MessageList()
        panel = ConversationPanel.__new__(ConversationPanel)
        panel._message_selection_mode = False
        panel._messages = [message]
        panel._message_rows = [message]
        panel.messages = message_list
        panel._speaker = SimpleNamespace(speak=Mock())
        panel.focused_message = lambda: message
        panel.selected_messages = lambda: [message] if message_list.selected else []
        panel._message_focus_key = lambda _message: ("id", message.message_id)
        panel._row_index_for_focus_key = lambda _key, fallback_index: 0
        panel._format_message_row = lambda _message: "Mensaje visible"
        panel._format_message_for_reader = lambda _message: "Mensaje completo"
        panel._update_message_action_buttons = Mock()

        self.assertTrue(ConversationPanel.begin_message_selection(panel))

        spoken = panel._speaker.speak.call_args.args[0]
        self.assertTrue(
            spoken.startswith("Modo de selección activado. Seleccionado. Mensaje completo")
        )
        self.assertEqual(message_list.labels[0], "Seleccionado. Mensaje visible")

    def test_focused_row_refreshes_accessible_selection_label(self) -> None:
        panel = SimpleNamespace(
            _message_selection_mode=True,
            _sync_native_message_selection=Mock(),
            _refresh_message_selection_labels=Mock(),
        )
        event = SimpleNamespace(Skip=Mock())

        ConversationPanel._on_message_focused(panel, event)

        panel._refresh_message_selection_labels.assert_called_once_with()
        event.Skip.assert_called_once_with()

    def test_focused_row_is_announced_with_state_and_message(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="other@example.test",
            body="mensaje",
        )
        panel = SimpleNamespace(
            _message_selection_mode=True,
            _refresh_message_selection_labels=Mock(),
            _message_at_row=Mock(return_value=message),
            _announce_focused_message_selection=Mock(),
        )
        event = SimpleNamespace(GetIndex=lambda: 0, Skip=Mock())

        ConversationPanel._on_message_focused(panel, event)

        panel._announce_focused_message_selection.assert_called_once_with(message)

    def test_selection_mode_disables_media_playback(self) -> None:
        panel = ConversationPanel.__new__(ConversationPanel)
        panel._message_selection_mode = True

        self.assertFalse(ConversationPanel.play_selected_audio(panel))
        self.assertFalse(ConversationPanel.play_selected_video(panel))

    def test_cancel_selection_clears_logical_messages(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="other@example.test",
            body="mensaje",
            message_id="message-id",
        )
        panel = ConversationPanel.__new__(ConversationPanel)
        panel._message_selection_mode = True
        panel._selected_message_keys = {
            ConversationPanel._message_focus_key(message),
        }
        panel._speaker = SimpleNamespace(speak=Mock())
        panel._clear_message_selection = Mock()
        panel._refresh_message_selection_labels = Mock()
        panel._update_message_action_buttons = Mock()
        panel.selected_messages = lambda: (
            [message] if panel._selected_message_keys else []
        )

        self.assertTrue(ConversationPanel.cancel_message_selection(panel))
        self.assertFalse(panel._message_selection_mode)
        self.assertEqual(panel._selected_message_keys, set())
        self.assertEqual(panel.selected_messages(), [])

    def test_copy_selected_messages_exits_selection_mode(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="other@example.test",
            body="mensaje",
            message_id="message-id",
        )
        window = MainWindow.__new__(MainWindow)
        window.conversation = SimpleNamespace(
            message_selection_mode=True,
            selected_messages=lambda: [message],
            cancel_message_selection=Mock(),
        )
        window.status_bar = SimpleNamespace(SetStatusText=Mock())
        window.speaker = SimpleNamespace(speak=Mock())
        clipboard = SimpleNamespace(
            Open=Mock(return_value=True),
            SetData=Mock(),
            Close=Mock(),
        )

        with patch("cliente_xmpp.ui.main_window.wx.TheClipboard", clipboard):
            MainWindow._copy_selected_messages(window)

        window.conversation.cancel_message_selection.assert_called_once_with()

    def test_context_selection_entry_focuses_message_before_starting(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="other@example.test",
            body="mensaje",
            message_id="message-id",
        )
        conversation = SimpleNamespace(
            focus_message=Mock(),
            begin_message_selection=Mock(),
        )
        window = MainWindow.__new__(MainWindow)
        window.conversation = conversation

        MainWindow._begin_message_selection_from_context(window, message)

        conversation.focus_message.assert_called_once_with(message)
        conversation.begin_message_selection.assert_called_once_with()

    def test_batch_delete_retracts_only_eligible_own_messages_and_removes_local_rows(self) -> None:
        chat = Chat(jid="chat@example.test", name="Chat")
        now = datetime.now().astimezone()
        own = Message(
            chat_jid=chat.jid,
            sender_jid="me@example.test",
            body="propio",
            outgoing=True,
            message_id="own-message",
            sent_at=now,
            delivery_state="sent",
        )
        incoming = Message(
            chat_jid=chat.jid,
            sender_jid="other@example.test",
            body="ajeno",
            message_id="incoming-message",
            sent_at=now + timedelta(seconds=1),
        )
        remaining: list[Message] = [own, incoming]
        window = MainWindow.__new__(MainWindow)
        window.whatsapp_verified = True
        window.current_jid = "me@example.test"
        window.messages_by_chat = {chat.jid: remaining}
        window.conversation = SimpleNamespace(
            current_chat=chat,
            selected_messages=lambda: [own, incoming],
            message_selection_mode=True,
            discard_message_media=Mock(),
            cancel_message_selection=Mock(),
            set_messages=Mock(),
            unread_marker_count=lambda: 0,
        )
        window.xmpp = SimpleNamespace(retract_message=Mock())
        window.message_store = SimpleNamespace(delete_cached_message=Mock())
        window.storage_executor = None
        window.status_bar = SimpleNamespace(SetStatusText=Mock())
        window.speaker = SimpleNamespace(speak=Mock())
        window._refresh_chat_order = Mock()

        with patch("cliente_xmpp.ui.main_window.wx.MessageBox", return_value=wx.YES):
            MainWindow._delete_selected_messages(window)

        window.xmpp.retract_message.assert_called_once_with(
            chat.jid,
            own.message_id,
            is_group=False,
        )
        self.assertEqual(window.messages_by_chat[chat.jid], [])
        self.assertEqual(window.message_store.delete_cached_message.call_count, 2)
        window.conversation.cancel_message_selection.assert_called_once_with()
        self.assertIn("no se pueden retirar", window.status_bar.SetStatusText.call_args.args[0])

    def test_local_batch_delete_removes_cached_incoming_message_too(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            message = Message(
                chat_jid="chat@example.test",
                sender_jid="other@example.test",
                body="ajeno",
                message_id="incoming-message",
            )
            store.upsert_messages("me@example.test", [message])

            store.delete_cached_message(
                "me@example.test",
                message.chat_jid,
                message.message_id,
            )

            self.assertEqual(
                store.load_recent_messages("me@example.test", message.chat_jid),
                [],
            )

    def test_managed_media_delete_does_not_touch_external_original(self) -> None:
        with TemporaryDirectory() as temp_dir:
            external = Path(temp_dir) / "original.jpg"
            external.write_bytes(b"original")
            message = Message(
                chat_jid="chat@example.test",
                sender_jid="me@example.test",
                body="",
                media_local_path=str(external),
            )

            deleted_path, error = delete_local_media_file(message, managed_only=True)

            self.assertIsNone(deleted_path)
            self.assertIsNone(error)
            self.assertTrue(external.exists())

    def test_batch_media_delete_preserves_external_reference(self) -> None:
        with TemporaryDirectory() as temp_dir:
            external = Path(temp_dir) / "original.jpg"
            external.write_bytes(b"original")
            chat = Chat(jid="chat@example.test", name="Chat")
            message = Message(
                chat_jid=chat.jid,
                sender_jid="other@example.test",
                body="",
                message_id="incoming-message",
                media_url="https://example.test/original.jpg",
                media_kind="image",
                media_local_path=str(external),
            )
            window = MainWindow.__new__(MainWindow)
            window.whatsapp_verified = False
            window.current_jid = "me@example.test"
            window.messages_by_chat = {chat.jid: [message]}
            window.conversation = SimpleNamespace(
                current_chat=chat,
                selected_messages=lambda: [message],
                message_selection_mode=True,
                discard_message_media=Mock(),
                cancel_message_selection=Mock(),
                set_messages=Mock(),
                unread_marker_count=lambda: 0,
            )
            window.message_store = SimpleNamespace(delete_cached_message=Mock())
            window.storage_executor = None
            window.status_bar = SimpleNamespace(SetStatusText=Mock())
            window.speaker = SimpleNamespace(speak=Mock())
            window._refresh_chat_order = Mock()

            with patch("cliente_xmpp.ui.main_window.wx.MessageBox", return_value=wx.YES):
                MainWindow._delete_selected_messages(window)

            self.assertEqual(message.media_local_path, str(external))
            self.assertTrue(external.exists())

    def test_forwarded_attachment_does_not_share_source_local_path(self) -> None:
        source = Message(
            chat_jid="source@example.test",
            sender_jid="other@example.test",
            body="foto",
            media_url="https://example.test/foto.jpg",
            media_kind="image",
            media_local_path="C:/external/foto.jpg",
        )
        target = Chat(jid="target@example.test", name="Destino")
        window = MainWindow.__new__(MainWindow)
        window.searchable_chats_by_jid = {target.jid: target}
        window.latest_message_timestamps_by_chat = {}
        window.conversation = SimpleNamespace(
            messages=SimpleNamespace(SetFocus=Mock()),
            message_selection_mode=True,
            cancel_message_selection=Mock(),
        )
        window.xmpp = SimpleNamespace(send_forward=Mock())
        window._require_whatsapp_connection = lambda: True
        window._add_pending_outgoing_message = Mock()
        window.status_bar = SimpleNamespace(SetStatusText=Mock())
        window.speaker = SimpleNamespace(speak=Mock())

        class Dialog:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def ShowModal(self) -> int:
                return wx.ID_OK

            def GetSelection(self) -> int:
                return 0

            def Destroy(self) -> None:
                pass

        with (
            patch.object(MainWindow, "_sort_chats_by_recency", return_value=[target]),
            patch("cliente_xmpp.ui.main_window.wx.SingleChoiceDialog", Dialog),
            patch(
                "cliente_xmpp.ui.main_window.wx.CallAfter",
                side_effect=lambda fn, *args: fn(*args),
            ),
        ):
            MainWindow._forward_messages(window, [source])

        forwarded = window._add_pending_outgoing_message.call_args.args[0]
        self.assertEqual(forwarded.media_local_path, "")
        window.conversation.cancel_message_selection.assert_called_once_with()

    def test_individual_external_media_delete_has_no_false_success(self) -> None:
        with TemporaryDirectory() as temp_dir:
            external = Path(temp_dir) / "original.jpg"
            external.write_bytes(b"original")
            message = Message(
                chat_jid="chat@example.test",
                sender_jid="other@example.test",
                body="",
                media_url="https://example.test/original.jpg",
                media_kind="image",
                media_local_path=str(external),
            )
            window = MainWindow.__new__(MainWindow)
            window._browser_message_in_memory = lambda current: current
            window.status_bar = SimpleNamespace(SetStatusText=Mock())
            window.conversation = SimpleNamespace(
                discard_message_media=Mock(),
                refresh_message=Mock(),
            )
            window._persist_message_media_path = Mock()

            with patch("cliente_xmpp.ui.main_window.wx.MessageBox", return_value=wx.YES):
                MainWindow._delete_browser_item(window, message)

            self.assertEqual(message.media_local_path, str(external))
            self.assertTrue(external.exists())
            window._persist_message_media_path.assert_not_called()
            window.conversation.discard_message_media.assert_not_called()
            self.assertIn("archivo externo", window.status_bar.SetStatusText.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
