from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.ui.main_window import MainWindow


class StickerGalleryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = wx.App.Get() or wx.App(False)

    def window(self):
        return SimpleNamespace(
            current_jid="account@example.test",
            conversation=SimpleNamespace(
                current_chat=Chat(jid="room@example.test", name="Fixture", is_group=True)
            ),
            _require_whatsapp_connection=Mock(return_value=True),
            _reply_metadata_for_attachment=Mock(
                return_value=(object(), "room@example.test/member", "quoted-id", "Quote")
            ),
            sticker_library=object(),
            settings_store=object(),
            status_bar=Mock(),
            xmpp=Mock(),
            _cancel_reply=Mock(),
            _mark_current_chat_displayed=Mock(),
        )

    def gallery(self):
        return SimpleNamespace(
            ShowModal=Mock(return_value=wx.ID_OK),
            Destroy=Mock(),
            selected_sticker=SimpleNamespace(
                name="Fixture", path="fixture.webp", description="Una figura saluda."
            ),
        )

    def test_send_uses_gallery_and_preserves_description_copy_and_group_reply(self) -> None:
        window, gallery = self.window(), self.gallery()
        with (
            patch("cliente_xmpp.ui.main_window.StickerGalleryDialog", return_value=gallery),
            patch("cliente_xmpp.ui.main_window.wx.FileDialog") as picker,
        ):
            MainWindow._on_send_sticker(window, None)
        picker.assert_not_called()
        window.xmpp.send_file.assert_called_once_with(
            "room@example.test",
            "fixture.webp",
            is_group=True,
            as_sticker=True,
            copy_sticker=True,
            sticker_description="Una figura saluda.",
            reply_to_jid="room@example.test/member",
            reply_to_id="quoted-id",
            reply_quote="Quote",
        )
        window._cancel_reply.assert_called_once()
        gallery.Destroy.assert_called_once()

    def test_cancel_or_account_change_never_sends_or_clears_reply(self) -> None:
        for change_account in (False, True):
            with self.subTest(change_account=change_account):
                window, gallery = self.window(), self.gallery()
                if change_account:

                    def account_changed(window=window):
                        window.current_jid = "other@example.test"
                        return wx.ID_OK

                    gallery.ShowModal.side_effect = account_changed
                else:
                    gallery.ShowModal.return_value = wx.ID_CANCEL
                with patch(
                    "cliente_xmpp.ui.main_window.StickerGalleryDialog", return_value=gallery
                ):
                    MainWindow._on_send_sticker(window, None)
                window.xmpp.send_file.assert_not_called()
                window._cancel_reply.assert_not_called()

    def test_share_pack_uses_explicit_native_send_and_account_guard(self) -> None:
        for switch_account in (False, True):
            with self.subTest(switch_account=switch_account):
                window, gallery = self.window(), self.gallery()

                def factory(
                    _parent,
                    _library,
                    _settings,
                    *,
                    on_share,
                    window=window,
                    gallery=gallery,
                    switch_account=switch_account,
                    **_kwargs,
                ):
                    def share():
                        if switch_account:
                            window.current_jid = "other@example.test"
                        on_share(Path("fixture.canstickers"))
                        return wx.ID_CANCEL

                    gallery.ShowModal.side_effect = share
                    return gallery

                with patch("cliente_xmpp.ui.main_window.StickerGalleryDialog", side_effect=factory):
                    MainWindow._on_send_sticker(window, None)
                if switch_account:
                    window.xmpp.send_file.assert_not_called()
                else:
                    window.xmpp.send_file.assert_called_once_with(
                        "room@example.test",
                        "fixture.canstickers",
                        is_group=True,
                        as_sticker_pack=True,
                    )

    def test_context_action_is_conditional_and_bound_to_popup_owner(self) -> None:
        for kind, filename, withdrawn, expected in (
            ("image", "photo.png", False, True),
            ("file", "photo.jpg", False, True),
            ("file", "document.pdf", False, False),
            ("image", "photo.png", True, False),
            ("", "", False, False),
        ):
            with self.subTest(kind=kind, filename=filename, withdrawn=withdrawn):
                message = Message(
                    chat_jid="chat@example.test",
                    sender_jid="sender@example.test",
                    body="",
                    media_kind=kind,
                    media_filename=filename,
                    retracted=withdrawn,
                    media_url="https://upload.example.test/file" if kind else "",
                )
                window = SimpleNamespace(
                    conversation=SimpleNamespace(selected_messages=lambda: []),
                    _private_message_recipient=Mock(return_value=None),
                    _message_can_be_edited=Mock(return_value=False),
                    _create_sticker_from_message=Mock(),
                )
                owner = Mock()
                bindings = []
                owner.Bind.side_effect = lambda _event, handler, item, bindings=bindings: (
                    bindings.append((item.GetItemLabelText(), handler))
                )
                MainWindow._show_message_context_menu(window, message, popup_parent=owner)
                labels = [label for label, _ in bindings]
                self.assertEqual("Crear sticker..." in labels, expected)
                if expected:
                    handler = next(
                        callback for label, callback in bindings if label == "Crear sticker..."
                    )
                    handler(None)
                    window._create_sticker_from_message.assert_called_once_with(message)

    def test_split_pack_caption_menu_imports_attachment_not_caption(self) -> None:
        from tests.test_sticker_pack_messages import StickerPackMessageTests

        attachment, caption = StickerPackMessageTests().messages()
        for withdrawn in (False, True):
            target = replace(attachment, retracted=withdrawn)
            window = SimpleNamespace(
                conversation=SimpleNamespace(selected_messages=lambda: []),
                messages_by_chat={caption.chat_jid: [target, caption]},
                _private_message_recipient=Mock(return_value=None),
                _message_can_be_edited=Mock(return_value=False),
                _import_sticker_pack_from_message=Mock(),
            )
            owner, bindings = Mock(), []
            owner.Bind.side_effect = lambda _event, handler, item, bindings=bindings: (
                bindings.append((item.GetItemLabelText(), handler))
            )
            MainWindow._show_message_context_menu(window, caption, popup_parent=owner)
            callbacks = [
                handler for label, handler in bindings if label == "Importar paquete de stickers..."
            ]
            self.assertEqual(len(callbacks), 0 if withdrawn else 1)
            if callbacks:
                callbacks[0](None)
                window._import_sticker_pack_from_message.assert_called_once_with(target)


if __name__ == "__main__":
    unittest.main()
