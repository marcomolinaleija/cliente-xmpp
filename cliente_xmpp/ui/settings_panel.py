from __future__ import annotations

import wx

WINDOWS_NOTIFICATIONS_LABEL = "Mostrar mensajes como notificaciones de Windows"
SHOW_PREVIEW_LABEL = "Mostrar el contenido del mensaje en la notificación"
ANNOUNCE_WITH_NVDA_LABEL = "Anunciar también el mensaje directamente con NVDA"
OPEN_CHAT_SOUND_LABEL = "Reproducir sonido para mensajes del chat abierto"
SENT_MESSAGE_SOUND_LABEL = "Reproducir sonido al enviar un mensaje"


def format_setting_state(label: str, enabled: bool) -> str:
    return f"{label}: {'activado' if enabled else 'desactivado'}"


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
            label=WINDOWS_NOTIFICATIONS_LABEL,
        )
        self.windows_notifications.SetToolTip(
            "Muestra una notificación nativa cuando llega un mensaje fuera del chat activo."
        )
        self.show_preview = wx.CheckBox(
            self,
            label=SHOW_PREVIEW_LABEL,
        )
        self.show_preview.SetToolTip(
            "Desactívalo para mostrar solamente que llegó un mensaje nuevo."
        )
        self.announce_with_nvda = wx.CheckBox(
            self,
            label=ANNOUNCE_WITH_NVDA_LABEL,
        )
        self.announce_with_nvda.SetToolTip(
            "Úsalo sólo si Windows o NVDA no anuncian la notificación nativa."
        )
        self.open_chat_sound = wx.CheckBox(
            self,
            label=OPEN_CHAT_SOUND_LABEL,
        )
        self.sent_message_sound = wx.CheckBox(
            self,
            label=SENT_MESSAGE_SOUND_LABEL,
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
        self.refresh_accessible_states()

    def refresh_accessible_states(self) -> None:
        for checkbox, base_label in self._checkboxes_with_labels():
            label = format_setting_state(base_label, checkbox.GetValue())
            checkbox.SetLabel(label)
            checkbox.SetName(label)
        self.Layout()

    def checkbox_state_text(self, checkbox: object) -> str:
        for candidate, base_label in self._checkboxes_with_labels():
            if candidate is checkbox:
                return format_setting_state(base_label, checkbox.GetValue())
        return "Configuración actualizada"

    def focus(self) -> None:
        self.windows_notifications.SetFocus()

    def _on_windows_notifications_changed(self, event: wx.CommandEvent) -> None:
        self._sync_windows_controls()
        self.refresh_accessible_states()
        event.Skip()

    def _sync_windows_controls(self) -> None:
        enabled = self.windows_notifications.GetValue()
        self.show_preview.Enable(enabled)
        self.announce_with_nvda.Enable(enabled)
        self.test_notification_button.Enable(enabled)

    def _checkboxes_with_labels(self) -> tuple[tuple[wx.CheckBox, str], ...]:
        return (
            (self.windows_notifications, WINDOWS_NOTIFICATIONS_LABEL),
            (self.show_preview, SHOW_PREVIEW_LABEL),
            (self.announce_with_nvda, ANNOUNCE_WITH_NVDA_LABEL),
            (self.open_chat_sound, OPEN_CHAT_SOUND_LABEL),
            (self.sent_message_sound, SENT_MESSAGE_SOUND_LABEL),
        )

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
