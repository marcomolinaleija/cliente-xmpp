from __future__ import annotations

import time
from collections.abc import Callable

import wx


class WhatsAppLinkPanel(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)

        self.message = wx.StaticText(self, label="")
        self.open_button = wx.Button(self, label="Generar QR")
        self.cancel_button = wx.Button(self, label="Cancelar vinculacion")
        self.cancel_button.Hide()

        self._layout()
        self.Hide()

    def set_status(
        self,
        text: str,
        action_label: str = "Generar QR",
        can_cancel: bool = False,
    ) -> None:
        self.message.SetLabel(text)
        self.open_button.SetLabel(action_label)
        self.cancel_button.Show(can_cancel)
        self.Show(True)
        self.Layout()

    def clear(self) -> None:
        self.cancel_button.Hide()
        self.Hide()

    def _layout(self) -> None:
        box = wx.BoxSizer(wx.HORIZONTAL)
        box.Add(self.message, 1, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 8)
        box.Add(self.open_button, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 8)
        box.Add(self.cancel_button, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 8)
        self.SetSizer(box)


class WhatsAppPairingCodeDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, code: str) -> None:
        super().__init__(parent, title="Codigo de vinculacion")

        self.code_value = code
        self.code = wx.TextCtrl(
            self,
            value=code,
            style=wx.TE_READONLY | wx.TE_CENTER,
        )
        self.code.SetToolTip("Codigo para escribir en WhatsApp.")
        font = self.code.GetFont()
        font.SetPointSize(28)
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.code.SetFont(font)
        self.code.SetMinSize((360, 64))
        self.copy_button = wx.Button(self, label="Copiar codigo")
        self.close_button = wx.Button(self, wx.ID_OK, "Cerrar")

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(
            wx.StaticText(
                self,
                label="Abre WhatsApp y escribe este codigo para vincular el cliente.",
            ),
            0,
            wx.ALL | wx.EXPAND,
            10,
        )
        box.Add(self.code, 0, wx.ALL | wx.EXPAND, 10)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer(1)
        buttons.Add(self.copy_button, 0, wx.ALL, 6)
        buttons.Add(self.close_button, 0, wx.ALL, 6)
        box.Add(buttons, 0, wx.EXPAND)
        self.SetSizerAndFit(box)
        self.SetMinSize((520, -1))
        self.copy_button.Bind(wx.EVT_BUTTON, self._on_copy)
        self.code.SetFocus()
        self.code.SelectAll()

    def _on_copy(self, _event: wx.CommandEvent) -> None:
        if not wx.TheClipboard.Open():
            return
        try:
            wx.TheClipboard.SetData(wx.TextDataObject(self.code_value))
        finally:
            wx.TheClipboard.Close()


class WhatsAppQrDialog(wx.Dialog):
    def __init__(
        self,
        parent: wx.Window,
        *,
        on_expired: Callable[[], None],
    ) -> None:
        super().__init__(
            parent,
            title="QR de vinculacion",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )

        display_rect = self._display_rect(parent)
        available_width = max(200, display_rect.width - 160)
        available_height = max(200, display_rect.height - 260)
        self.qr_size = min(520, available_width, available_height)
        self.status = wx.StaticText(
            self,
            label="Solicitando un QR a WhatsApp...",
            style=wx.ALIGN_CENTER_HORIZONTAL,
        )
        self.status.SetName("Estado de vinculacion de WhatsApp")
        self.qr = wx.StaticBitmap(self, bitmap=wx.Bitmap(1, 1))
        self.qr.Hide()
        self.retry_button = wx.Button(self, label="Generar nuevo QR")
        self.retry_button.Hide()
        self.cancel_link_button = wx.Button(self, wx.ID_STOP, "Cancelar vinculacion")
        self.cancel_link_button.Disable()
        self.close_button = wx.Button(self, wx.ID_CANCEL, "Cerrar")
        self.close_button.Bind(wx.EVT_BUTTON, lambda _event: self.Close())
        self._deadline = 0.0
        self._state = "pending"
        self._expired_notified = False
        self._last_countdown_value = -1
        self._on_expired = on_expired
        self._timer = wx.Timer(self)

        box = wx.BoxSizer(wx.VERTICAL)
        box.AddStretchSpacer(1)
        box.Add(self.status, 0, wx.ALL | wx.EXPAND, 12)
        box.Add(self.qr, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL, 8)
        box.AddStretchSpacer(1)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer(1)
        buttons.Add(self.retry_button, 0, wx.ALL, 6)
        buttons.Add(self.cancel_link_button, 0, wx.ALL, 6)
        buttons.Add(self.close_button, 0, wx.ALL, 6)
        buttons.AddStretchSpacer(1)
        box.Add(buttons, 0, wx.EXPAND)
        self.SetSizer(box)
        self.SetMinSize((min(480, display_rect.width - 40), 180))
        self.Bind(wx.EVT_TIMER, self._on_timer, self._timer)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)
        self._fit_to_content()
        self.close_button.SetFocus()

    def set_pending(self, deadline: float, *, can_cancel: bool) -> None:
        self._deadline = deadline
        self._state = "pending"
        self._expired_notified = False
        self._last_countdown_value = -1
        self.qr.Hide()
        self.retry_button.Hide()
        self.cancel_link_button.Enable(can_cancel)
        self._start_timer()
        self._update_countdown()
        self._fit_to_content()

    def set_image(self, image_path: str, deadline: float, *, can_cancel: bool) -> bool:
        bitmap = self._bitmap_from_path(image_path)
        if bitmap is None:
            return False
        self._deadline = deadline
        self._state = "ready"
        self._expired_notified = False
        self._last_countdown_value = -1
        self.qr.SetBitmap(bitmap)
        self.qr.Show()
        self.retry_button.Hide()
        self.cancel_link_button.Enable(can_cancel)
        self._start_timer()
        self._update_countdown()
        self._fit_to_content()
        return True

    def set_expired(self, message: str, *, can_cancel: bool) -> None:
        self._deadline = 0.0
        self._state = "expired"
        self._timer.Stop()
        self.status.SetLabel(message)
        self.qr.Hide()
        self.retry_button.Show()
        self.cancel_link_button.Enable(can_cancel)
        self._fit_to_content()

    def set_error(self, message: str, *, can_cancel: bool) -> None:
        self._deadline = 0.0
        self._state = "error"
        self._timer.Stop()
        self.status.SetLabel(message)
        self.qr.Hide()
        self.retry_button.Show()
        self.cancel_link_button.Enable(can_cancel)
        self._fit_to_content()

    def set_can_cancel(self, can_cancel: bool) -> None:
        self.cancel_link_button.Enable(can_cancel)

    def _start_timer(self) -> None:
        if not self._timer.IsRunning():
            self._timer.Start(1000)

    def _update_countdown(self) -> None:
        remaining = max(0, int(self._deadline - time.monotonic() + 0.999))
        displayed_remaining = (
            remaining if remaining <= 10 else ((remaining + 9) // 10) * 10
        )
        if displayed_remaining == self._last_countdown_value:
            return
        self._last_countdown_value = displayed_remaining
        if self._state == "ready":
            self.status.SetLabel(
                "Escanea este QR desde WhatsApp. "
                f"Tiempo restante: {displayed_remaining} segundos."
            )
        else:
            self.status.SetLabel(
                "Solicitando un QR a WhatsApp. "
                f"Tiempo restante: {displayed_remaining} segundos."
            )

    def _on_timer(self, _event: wx.TimerEvent) -> None:
        if self._deadline <= 0:
            self._timer.Stop()
            return
        if time.monotonic() < self._deadline:
            self._update_countdown()
            return

        self._timer.Stop()
        if self._expired_notified:
            return
        self._expired_notified = True
        self.set_expired(
            "El QR expiro. Genera uno nuevo para volver a intentarlo.",
            can_cancel=self.cancel_link_button.IsEnabled(),
        )
        self._on_expired()

    def _on_destroy(self, event: wx.WindowDestroyEvent) -> None:
        if event.GetEventObject() is self:
            self._timer.Stop()
        event.Skip()

    def _fit_to_content(self) -> None:
        display_rect = self._display_rect(self.GetParent())
        if self.IsMaximized():
            self.status.Wrap(max(200, self.GetClientSize().width - 24))
            self.Layout()
            return

        self.Layout()
        best = self.GetBestSize()
        width = min(display_rect.width - 40, max(480, best.width))
        self.status.Wrap(max(200, width - 24))
        self.Layout()
        best = self.GetBestSize()
        height = min(display_rect.height - 40, max(180, best.height))
        self.SetSize((width, height))
        self.SetPosition(
            (
                display_rect.x + max(0, (display_rect.width - width) // 2),
                display_rect.y + max(0, (display_rect.height - height) // 2),
            )
        )

    def _bitmap_from_path(self, image_path: str) -> wx.Bitmap | None:
        if not wx.Image.CanRead(image_path):
            return None

        image = wx.Image(image_path)
        if image.IsOk():
            image = image.Scale(self.qr_size, self.qr_size, wx.IMAGE_QUALITY_NEAREST)
            return wx.Bitmap(image)

        return None

    @staticmethod
    def _display_rect(parent: wx.Window) -> wx.Rect:
        display_index = wx.Display.GetFromWindow(parent)
        if display_index == wx.NOT_FOUND:
            return wx.GetClientDisplayRect()
        return wx.Display(display_index).GetClientArea()
