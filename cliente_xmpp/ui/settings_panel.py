from __future__ import annotations

import wx

from cliente_xmpp.config.settings import (
    CONNECTION_MODE_LOCAL,
    CONNECTION_MODE_REMOTE,
    DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES,
    UPDATE_CHECK_INTERVAL_CHOICES,
)

WINDOWS_NOTIFICATIONS_LABEL = "Mostrar mensajes como notificaciones de Windows"
SHOW_PREVIEW_LABEL = "Mostrar el contenido del mensaje en la notificación"
ANNOUNCE_WITH_NVDA_LABEL = "Anunciar también el mensaje directamente con NVDA"
OPEN_CHAT_SOUND_LABEL = "Reproducir sonido para mensajes del chat abierto"
SENT_MESSAGE_SOUND_LABEL = "Reproducir sonido al enviar un mensaje"
MINIMIZE_TO_TRAY_ON_ALT_F4_LABEL = "Minimizar a la bandeja al usar Alt+F4"
UPDATE_CHECK_INTERVAL_LABEL = "Buscar actualizaciones automáticamente"
UPDATE_CHECK_INTERVAL_LABELS = {
    20: "Cada 20 minutos",
    30: "Cada 30 minutos",
    60: "Cada hora",
    300: "Cada 5 horas",
    None: "Nunca",
}
CONNECTION_MODE_LABELS = {
    CONNECTION_MODE_LOCAL: "Puente local (WSL2)",
    CONNECTION_MODE_REMOTE: "Servidor XMPP",
}


def format_setting_state(label: str, enabled: bool) -> str:
    return f"{label}: {'activado' if enabled else 'desactivado'}"


class SettingsPanel(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self._update_check_runtime_available = False
        self._local_bridge_available = False

        self.title = wx.StaticText(self, label="Configuración")
        title_font = self.title.GetFont()
        title_font.SetPointSize(title_font.GetPointSize() + 3)
        title_font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.title.SetFont(title_font)

        self.connection_mode = wx.Choice(
            self,
            choices=list(CONNECTION_MODE_LABELS.values()),
        )
        self.connection_mode.SetName("Tipo de conexión")
        self.connection_mode.SetToolTip(
            "Elige que perfil se usara la proxima vez que abras la aplicacion."
        )
        self.connection_mode_status = wx.StaticText(self, label="")

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
        self.incoming_sound_path = wx.TextCtrl(self, style=wx.TE_READONLY)
        self.incoming_sound_path.SetName("Sonido para mensajes entrantes")
        self.incoming_sound_path.SetToolTip(
            "Archivo usado para mensajes entrantes. Puedes elegir un sonido de Windows "
            "o uno personalizado."
        )
        self.choose_incoming_sound_button = wx.Button(self, label="Elegir sonido...")
        self.reset_incoming_sound_button = wx.Button(self, label="Usar predeterminado")
        self.minimize_to_tray_on_alt_f4 = wx.CheckBox(
            self,
            label=MINIMIZE_TO_TRAY_ON_ALT_F4_LABEL,
        )
        self.minimize_to_tray_on_alt_f4.SetToolTip(
            "Oculta la ventana y la deja disponible desde el icono de la bandeja del sistema."
        )
        self.update_check_interval = wx.ComboBox(
            self,
            choices=[UPDATE_CHECK_INTERVAL_LABELS[value] for value in UPDATE_CHECK_INTERVAL_CHOICES]
            + [UPDATE_CHECK_INTERVAL_LABELS[None]],
            style=wx.CB_READONLY,
        )
        self.update_check_interval.SetName(UPDATE_CHECK_INTERVAL_LABEL)
        self.update_check_interval.SetToolTip(
            "Busca releases nuevas en segundo plano con la frecuencia elegida."
        )
        self.check_updates_button = wx.Button(self, label="Buscar actualizaciones ahora")
        self.check_updates_button.SetToolTip(
            "Busca una actualización ahora, sin bloquear la aplicación."
        )
        self.update_check_status = wx.StaticText(self, label="")

        self.test_notification_button = wx.Button(
            self,
            label="Probar notificación de Windows",
        )
        self.back_button = wx.Button(self, label="&Volver")

        self._layout()

    def set_values(
        self,
        *,
        windows_notifications: bool,
        show_preview: bool,
        announce_with_nvda: bool,
        open_chat_sound: bool,
        sent_message_sound: bool,
        incoming_sound_path: str,
        minimize_to_tray_on_alt_f4: bool,
        update_check_interval_minutes: int | None = DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES,
        connection_mode: str = CONNECTION_MODE_REMOTE,
        local_bridge_available: bool = False,
    ) -> None:
        self._local_bridge_available = local_bridge_available
        self.set_connection_mode(connection_mode)
        self.windows_notifications.SetValue(windows_notifications)
        self.show_preview.SetValue(show_preview)
        self.announce_with_nvda.SetValue(announce_with_nvda)
        self.open_chat_sound.SetValue(open_chat_sound)
        self.sent_message_sound.SetValue(sent_message_sound)
        self.incoming_sound_path.SetValue(
            incoming_sound_path or "Sonido predeterminado de WhatsApp CAN"
        )
        self.minimize_to_tray_on_alt_f4.SetValue(minimize_to_tray_on_alt_f4)
        self.set_update_check_interval_minutes(update_check_interval_minutes)
        self.refresh_accessible_states()

    def connection_mode_value(self) -> str:
        selected = self.connection_mode.GetStringSelection()
        for mode, label in CONNECTION_MODE_LABELS.items():
            if selected == label:
                return mode
        return CONNECTION_MODE_REMOTE

    def set_connection_mode(self, mode: str) -> None:
        label = CONNECTION_MODE_LABELS.get(mode, CONNECTION_MODE_LABELS[CONNECTION_MODE_REMOTE])
        self.connection_mode.SetStringSelection(label)

    def set_connection_mode_status(self, status: str) -> None:
        self.connection_mode_status.SetLabel(status)
        self.Layout()

    def update_check_interval_minutes(self) -> int | None:
        label = self.update_check_interval.GetValue()
        for interval, interval_label in UPDATE_CHECK_INTERVAL_LABELS.items():
            if label == interval_label:
                return interval
        return DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES

    def set_update_check_interval_minutes(self, interval: int | None) -> None:
        label = UPDATE_CHECK_INTERVAL_LABELS.get(
            interval,
            UPDATE_CHECK_INTERVAL_LABELS[DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES],
        )
        self.update_check_interval.SetValue(label)

    def set_update_check_runtime_available(self, available: bool) -> None:
        self._update_check_runtime_available = available
        self.update_check_interval.Enable(available)
        self.check_updates_button.Enable(available)
        if available:
            self.update_check_status.SetLabel("")
        else:
            self.update_check_status.SetLabel(
                "Disponible al ejecutar la aplicación instalada."
            )

    def set_update_check_in_progress(self, in_progress: bool) -> None:
        self.check_updates_button.Enable(
            self._update_check_runtime_available and not in_progress
        )
        if in_progress:
            self.update_check_status.SetLabel("Buscando actualizaciones en segundo plano...")

    def set_update_check_status(self, status: str) -> None:
        self.update_check_status.SetLabel(status)

    def refresh_accessible_states(self) -> None:
        self._apply_control_state()

    def apply_interactive_state(self) -> None:
        wx.CallAfter(self._apply_control_state)

    def checkbox_state_text(self, checkbox: object) -> str:
        for candidate, base_label in self._checkboxes_with_labels():
            if candidate is checkbox:
                return format_setting_state(base_label, checkbox.GetValue())
        return "Configuración actualizada"

    def focus(self) -> None:
        self.connection_mode.SetFocus()

    def _sync_windows_controls(self) -> None:
        enabled = self.windows_notifications.GetValue()
        for control in (
            self.show_preview,
            self.announce_with_nvda,
            self.test_notification_button,
        ):
            if control.IsEnabled() != enabled:
                control.Enable(enabled)

    def _apply_control_state(self) -> None:
        self.Freeze()
        try:
            self._sync_windows_controls()
            for checkbox, base_label in self._checkboxes_with_labels():
                label = format_setting_state(base_label, checkbox.GetValue())
                if checkbox.GetLabel() != label:
                    checkbox.SetLabel(label)
                if checkbox.GetName() != label:
                    checkbox.SetName(label)
            self.Layout()
        finally:
            self.Thaw()

        for control in (
            self.windows_notifications,
            self.show_preview,
            self.announce_with_nvda,
            self.open_chat_sound,
            self.sent_message_sound,
            self.test_notification_button,
        ):
            control.Refresh()
        self.Refresh()
        parent = self.GetParent()
        if parent is not None:
            parent.Layout()
            parent.Refresh()

    def _checkboxes_with_labels(self) -> tuple[tuple[wx.CheckBox, str], ...]:
        return (
            (self.windows_notifications, WINDOWS_NOTIFICATIONS_LABEL),
            (self.show_preview, SHOW_PREVIEW_LABEL),
            (self.announce_with_nvda, ANNOUNCE_WITH_NVDA_LABEL),
            (self.open_chat_sound, OPEN_CHAT_SOUND_LABEL),
            (self.sent_message_sound, SENT_MESSAGE_SOUND_LABEL),
            (self.minimize_to_tray_on_alt_f4, MINIMIZE_TO_TRAY_ON_ALT_F4_LABEL),
        )

    def _layout(self) -> None:
        connection_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Conexión")
        connection_box.Add(wx.StaticText(self, label="Usar al iniciar:"), 0, wx.ALL, 8)
        connection_box.Add(
            self.connection_mode,
            0,
            wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND,
            8,
        )
        connection_box.Add(
            self.connection_mode_status,
            0,
            wx.LEFT | wx.RIGHT | wx.BOTTOM,
            8,
        )

        notification_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Notificaciones")
        notification_box.Add(self.windows_notifications, 0, wx.ALL, 8)
        notification_box.Add(self.show_preview, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        notification_box.Add(self.announce_with_nvda, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        notification_box.Add(self.open_chat_sound, 0, wx.ALL, 8)
        notification_box.Add(self.sent_message_sound, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        notification_box.Add(
            wx.StaticText(self, label="Sonido de mensajes entrantes:"),
            0,
            wx.LEFT | wx.RIGHT | wx.TOP,
            8,
        )
        notification_box.Add(self.incoming_sound_path, 0, wx.ALL | wx.EXPAND, 8)
        incoming_sound_buttons = wx.BoxSizer(wx.HORIZONTAL)
        incoming_sound_buttons.Add(self.choose_incoming_sound_button, 0, wx.RIGHT, 8)
        incoming_sound_buttons.Add(self.reset_incoming_sound_button, 0)
        notification_box.Add(incoming_sound_buttons, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        window_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Ventana")
        window_box.Add(self.minimize_to_tray_on_alt_f4, 0, wx.ALL, 8)

        updates_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Actualizaciones")
        updates_box.Add(wx.StaticText(self, label=UPDATE_CHECK_INTERVAL_LABEL), 0, wx.ALL, 8)
        updates_box.Add(
            self.update_check_interval,
            0,
            wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND,
            8,
        )
        updates_box.Add(self.check_updates_button, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        updates_box.Add(self.update_check_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self.test_notification_button, 0, wx.RIGHT, 8)
        buttons.Add(self.back_button, 0)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.title, 0, wx.ALL, 16)
        box.Add(connection_box, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 16)
        box.Add(notification_box, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 16)
        box.Add(window_box, 0, wx.ALL | wx.EXPAND, 16)
        box.Add(updates_box, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 16)
        box.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 16)
        box.AddStretchSpacer(1)
        self.SetSizer(box)
