from __future__ import annotations

import wx


class SettingsPanel(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)

        self.title = wx.StaticText(self, label="Configuración")
        title_font = self.title.GetFont()
        title_font.SetPointSize(title_font.GetPointSize() + 3)
        title_font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.title.SetFont(title_font)

        self.windows_notifications = wx.CheckBox(
            self,
            label="Mostrar mensajes como notificaciones de Windows",
        )
        self.windows_notifications.SetToolTip(
            "Muestra una notificación nativa cuando llega un mensaje fuera del chat activo."
        )
        self.show_preview = wx.CheckBox(
            self,
            label="Mostrar el contenido del mensaje en la notificación",
        )
        self.show_preview.SetToolTip(
            "Desactívalo para mostrar solamente que llegó un mensaje nuevo."
        )
        self.announce_with_nvda = wx.CheckBox(
            self,
            label="Anunciar también el mensaje directamente con NVDA",
        )
        self.announce_with_nvda.SetToolTip(
            "Úsalo sólo si Windows o NVDA no anuncian la notificación nativa."
        )
        self.open_chat_sound = wx.CheckBox(
            self,
            label="Reproducir sonido para mensajes del chat abierto",
        )
        self.sent_message_sound = wx.CheckBox(
            self,
            label="Reproducir sonido al enviar un mensaje",
        )

        self.test_notification_button = wx.Button(
            self,
            label="Probar notificación de Windows",
        )
        self.back_button = wx.Button(self, label="&Volver")

        self.windows_notifications.Bind(wx.EVT_CHECKBOX, self._on_windows_notifications_changed)
        self._layout()

    def set_values(
        self,
        *,
        windows_notifications: bool,
        show_preview: bool,
        announce_with_nvda: bool,
        open_chat_sound: bool,
        sent_message_sound: bool,
    ) -> None:
        self.windows_notifications.SetValue(windows_notifications)
        self.show_preview.SetValue(show_preview)
        self.announce_with_nvda.SetValue(announce_with_nvda)
        self.open_chat_sound.SetValue(open_chat_sound)
        self.sent_message_sound.SetValue(sent_message_sound)
        self._sync_windows_controls()

    def focus(self) -> None:
        self.windows_notifications.SetFocus()

    def _on_windows_notifications_changed(self, event: wx.CommandEvent) -> None:
        self._sync_windows_controls()
        event.Skip()

    def _sync_windows_controls(self) -> None:
        enabled = self.windows_notifications.GetValue()
        self.show_preview.Enable(enabled)
        self.announce_with_nvda.Enable(enabled)
        self.test_notification_button.Enable(enabled)

    def _layout(self) -> None:
        notification_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Notificaciones")
        notification_box.Add(self.windows_notifications, 0, wx.ALL, 8)
        notification_box.Add(self.show_preview, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        notification_box.Add(self.announce_with_nvda, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        notification_box.Add(self.open_chat_sound, 0, wx.ALL, 8)
        notification_box.Add(self.sent_message_sound, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self.test_notification_button, 0, wx.RIGHT, 8)
        buttons.Add(self.back_button, 0)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.title, 0, wx.ALL, 16)
        box.Add(notification_box, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 16)
        box.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 16)
        box.AddStretchSpacer(1)
        self.SetSizer(box)
