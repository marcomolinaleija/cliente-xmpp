from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx

from cliente_xmpp.ui.main_window import MainWindow


class DisconnectActionTests(unittest.TestCase):
    def _window(self) -> SimpleNamespace:
        return SimpleNamespace(
            connection_header=Mock(),
            status_bar=Mock(),
            xmpp=Mock(),
        )

    def test_disconnect_keeps_connection_when_confirmation_is_declined(self) -> None:
        window = self._window()

        with patch("cliente_xmpp.ui.main_window.wx.MessageBox", return_value=wx.NO) as dialog:
            MainWindow._on_disconnect(window, Mock())

        dialog.assert_called_once()
        message = dialog.call_args.args[0]
        self.assertIn("No desvincula WhatsApp", message)
        self.assertIn("Tampoco borra tus chats", message)
        window.xmpp.disconnect.assert_not_called()

    def test_disconnect_runs_only_after_explicit_confirmation(self) -> None:
        window = self._window()

        with patch("cliente_xmpp.ui.main_window.wx.MessageBox", return_value=wx.YES):
            MainWindow._on_disconnect(window, Mock())

        window.connection_header.set_status.assert_called_once_with("Desconectando...")
        window.status_bar.SetStatusText.assert_called_once_with("Desconectando...")
        window.xmpp.disconnect.assert_called_once_with()
