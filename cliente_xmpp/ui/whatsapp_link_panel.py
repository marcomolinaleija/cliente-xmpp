from __future__ import annotations

from dataclasses import dataclass

import wx


@dataclass(frozen=True, slots=True)
class WhatsAppLinkAction:
    mode: str
    phone: str = ""


class WhatsAppLinkPanel(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)

        self.message = wx.StaticText(self, label="")
        self.open_button = wx.Button(self, label="Vincular WhatsApp")

        self._layout()
        self.Hide()

    def set_status(self, text: str, action_label: str = "Vincular WhatsApp") -> None:
        self.message.SetLabel(text)
        self.open_button.SetLabel(action_label)
        self.Show(True)
        self.Layout()

    def clear(self) -> None:
        self.Hide()

    def _layout(self) -> None:
        box = wx.BoxSizer(wx.HORIZONTAL)
        box.Add(self.message, 1, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 8)
        box.Add(self.open_button, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 8)
        self.SetSizer(box)


class WhatsAppLinkDialog(wx.Dialog):
    def __init__(
        self,
        parent: wx.Window,
        component_jid: str,
        status_text: str,
        last_phone: str = "",
    ) -> None:
        super().__init__(parent, title="Vincular WhatsApp")

        self.component_jid = component_jid
        self.status_text = wx.StaticText(self, label=status_text)
        self.phone = wx.TextCtrl(self, value=last_phone)
        self.phone.SetToolTip(
            "Telefono de WhatsApp en formato internacional, por ejemplo +5218123456789."
        )
        self.code_button = wx.Button(self, wx.ID_OK, "Obtener codigo")
        self.qr_button = wx.Button(self, wx.ID_APPLY, "Solicitar QR")
        self.cancel_button = wx.Button(self, wx.ID_CANCEL, "Cancelar")
        self.action: WhatsAppLinkAction | None = None

        self._layout()
        self._bind_events()
        self.phone.SetFocus()

    def _layout(self) -> None:
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.status_text, 0, wx.ALL | wx.EXPAND, 10)
        box.Add(wx.StaticText(self, label=f"Componente: {self.component_jid}"), 0, wx.ALL, 10)
        box.Add(
            wx.StaticText(
                self,
                label="Recomendado: usa codigo por telefono. El QR puede perderse si cierras la app.",
            ),
            0,
            wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND,
            10,
        )
        box.Add(wx.StaticText(self, label="Telefono:"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        box.Add(self.phone, 0, wx.ALL | wx.EXPAND, 10)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer(1)
        buttons.Add(self.code_button, 0, wx.ALL, 6)
        buttons.Add(self.qr_button, 0, wx.ALL, 6)
        buttons.Add(self.cancel_button, 0, wx.ALL, 6)
        box.Add(buttons, 0, wx.EXPAND)

        self.SetSizerAndFit(box)
        self.SetMinSize((520, -1))

    def _bind_events(self) -> None:
        self.code_button.Bind(wx.EVT_BUTTON, self._on_code)
        self.qr_button.Bind(wx.EVT_BUTTON, self._on_qr)

    def _on_code(self, _event: wx.CommandEvent) -> None:
        phone = self.phone.GetValue().strip()
        if not phone:
            wx.MessageBox(
                "Escribe el telefono de WhatsApp en formato internacional.",
                "Telefono requerido",
            )
            self.phone.SetFocus()
            return

        self.action = WhatsAppLinkAction(mode="code", phone=phone)
        self.EndModal(wx.ID_OK)

    def _on_qr(self, _event: wx.CommandEvent) -> None:
        self.action = WhatsAppLinkAction(mode="qr")
        self.EndModal(wx.ID_APPLY)


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
    def __init__(self, parent: wx.Window, image_path: str) -> None:
        super().__init__(
            parent,
            title="QR de vinculacion",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )

        display_width, display_height = wx.GetDisplaySize()
        self.qr_size = max(420, min(display_width - 160, display_height - 220, 820))
        self.qr = wx.StaticBitmap(self, bitmap=self._bitmap_from_path(image_path))
        self.close_button = wx.Button(self, wx.ID_OK, "Cerrar")
        self.close_button.Bind(wx.EVT_BUTTON, lambda _event: self.Close())

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(
            wx.StaticText(self, label="Escanea este QR desde WhatsApp en tu telefono."),
            0,
            wx.ALL | wx.EXPAND,
            10,
        )
        box.Add(self.qr, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL, 10)
        box.Add(self.close_button, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        self.SetSizer(box)
        self.SetMinSize(
            (
                min(display_width - 40, self.qr_size + 100),
                min(display_height - 40, self.qr_size + 170),
            )
        )
        self.SetSize((display_width - 40, display_height - 40))
        self.Layout()
        self.CentreOnParent()
        self.Maximize(True)
        self.close_button.SetFocus()

    def set_image(self, image_path: str) -> None:
        self.qr.SetBitmap(self._bitmap_from_path(image_path))
        self.Layout()

    def _bitmap_from_path(self, image_path: str) -> wx.Bitmap:
        image = wx.Image(image_path)
        if image.IsOk():
            image = image.Scale(self.qr_size, self.qr_size, wx.IMAGE_QUALITY_NEAREST)
            return wx.Bitmap(image)

        return wx.Bitmap(self.qr_size, self.qr_size)
