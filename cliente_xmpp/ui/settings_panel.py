"""Preferencias de WhatsApp CAN: un diálogo con pestañas y Guardar/Cancelar/Aplicar.

Los controles conservan los nombres del antiguo panel incrustado para que la ventana
principal y los flujos en segundo plano (actualizaciones, puente local) sigan
actualizándolos. Nada se guarda al marcar una casilla: sólo Aplicar o Guardar
confirman los cambios; Cancelar o Escape los descarta.
"""

from __future__ import annotations

from collections.abc import Callable

import wx

from cliente_xmpp.config.settings import (
    CONNECTION_MODE_LOCAL,
    CONNECTION_MODE_REMOTE,
    DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES,
    UPDATE_CHECK_INTERVAL_CHOICES,
)
from cliente_xmpp.models.transcription import (
    AUTO_ALL,
    AUTO_ALL_EXCEPT,
    AUTO_OFF,
    AUTO_SELECTED,
    DELIVERY_PRIVATE,
    DELIVERY_REPLY,
    TranscriptionSettings,
    TranscriptionSettingsUpdate,
    validate_api_key,
)

WINDOWS_NOTIFICATIONS_LABEL = "Mostrar mensajes como notificaciones de Windows"
SHOW_PREVIEW_LABEL = "Mostrar el contenido del mensaje en la notificación"
ANNOUNCE_WITH_NVDA_LABEL = "Anunciar también el mensaje directamente con NVDA"
OPEN_CHAT_SOUND_LABEL = "Reproducir sonido para mensajes del chat abierto"
SENT_MESSAGE_SOUND_LABEL = "Reproducir sonido al enviar un mensaje"
MINIMIZE_TO_TRAY_ON_ALT_F4_LABEL = "Minimizar a la bandeja al usar Alt+F4"
EMOTICON_CONVERSION_LABEL = "Convertir emoticonos como :-) o xD en emojis al escribir"
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
AUTO_MODE_LABELS = (
    (AUTO_OFF, "Desactivadas"),
    (AUTO_ALL, "En todos los chats y grupos"),
    (AUTO_SELECTED, "Sólo en los chats seleccionados"),
    (AUTO_ALL_EXCEPT, "En todos excepto los chats seleccionados"),
)
DELIVERY_LABELS = (
    (DELIVERY_PRIVATE, "Privadas: sólo yo las veo en este cliente"),
    (DELIVERY_REPLY, "Enviarlas al chat como respuesta a la nota de voz"),
)
PAGE_TITLES = ("General", "Notificaciones y sonidos", "Mensajes", "Transcripción", "Integraciones")


def format_setting_state(label: str, enabled: bool) -> str:
    return f"{label}: {'activado' if enabled else 'desactivado'}"


class PreferencesDialog(wx.Dialog):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(
            parent,
            title="Preferencias",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._update_check_runtime_available = False
        self._local_bridge_available = False
        self._bridge_update_in_progress = False
        self.apply_handler: Callable[[], bool] | None = None
        self.transcription_reload_handler: Callable[[], None] | None = None
        self._transcription_settings: TranscriptionSettings | None = None
        self._transcription_chats: list[tuple[str, str]] = []
        self._transcription_checked: set[str] = set()
        self._visible_chat_jids: list[str] = []
        # Native look: the yellow-on-blue theme would owner-draw the checkboxes.
        self.keep_native_appearance = True

        self.notebook = wx.Notebook(self)
        self.notebook.SetName("Categorías de preferencias")
        self._build_general_page()
        self._build_notifications_page()
        self._build_messages_page()
        self._build_transcription_page()
        self._build_integrations_page()

        self.save_button = wx.Button(self, wx.ID_OK, "&Guardar")
        self.cancel_button = wx.Button(self, wx.ID_CANCEL, "Cancelar")
        self.apply_button = wx.Button(self, wx.ID_APPLY, "&Aplicar")
        self.save_button.SetDefault()
        self.SetEscapeId(wx.ID_CANCEL)
        # Compatibilidad: algunos flujos aún enlazan "Volver".
        self.back_button = self.cancel_button

        buttons = wx.StdDialogButtonSizer()
        buttons.AddButton(self.save_button)
        buttons.AddButton(self.cancel_button)
        buttons.AddButton(self.apply_button)
        buttons.Realize()

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.notebook, 1, wx.ALL | wx.EXPAND, 10)
        box.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        self.SetSizer(box)
        self.SetMinSize((640, 560))
        self.Fit()

        self.save_button.Bind(wx.EVT_BUTTON, self._on_save)
        self.apply_button.Bind(wx.EVT_BUTTON, self._on_apply)
        self.windows_notifications.Bind(wx.EVT_CHECKBOX, self._on_dependent_toggle)
        self.transcription_service.Bind(wx.EVT_CHECKBOX, self._on_dependent_toggle)
        self.transcription_auto_mode.Bind(wx.EVT_CHOICE, self._on_dependent_toggle)
        self.clear_api_key.Bind(wx.EVT_CHECKBOX, self._on_dependent_toggle)
        self.transcription_chat_filter.Bind(wx.EVT_TEXT, self._on_chat_filter)
        self.transcription_chats.Bind(wx.EVT_LIST_ITEM_CHECKED, self._on_chat_checked)
        self.transcription_chats.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self._on_chat_checked)
        self.transcription_reload_button.Bind(wx.EVT_BUTTON, self._on_transcription_reload)

    # -- construcción ----------------------------------------------------------
    def _page(self, title: str) -> tuple[wx.Panel, wx.BoxSizer]:
        page = wx.Panel(self.notebook)
        page.SetName(title)
        sizer = wx.BoxSizer(wx.VERTICAL)
        page.SetSizer(sizer)
        self.notebook.AddPage(page, title)
        return page, sizer

    @staticmethod
    def _labelled(page: wx.Window, sizer: wx.Sizer, label: str, control: wx.Window) -> None:
        # The label precedes its control so NVDA reads them together.
        text = wx.StaticText(page, label=label)
        sizer.Add(text, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        sizer.Add(control, 0, wx.ALL | wx.EXPAND, 8)
        control.SetName(label.rstrip(":"))

    def _build_general_page(self) -> None:
        page, sizer = self._page(PAGE_TITLES[0])
        self.connection_mode = wx.Choice(page, choices=list(CONNECTION_MODE_LABELS.values()))
        self._labelled(page, sizer, "Conexión al iniciar:", self.connection_mode)
        self.connection_mode_status = wx.StaticText(page, label="")
        sizer.Add(self.connection_mode_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.minimize_to_tray_on_alt_f4 = wx.CheckBox(
            page, label=MINIMIZE_TO_TRAY_ON_ALT_F4_LABEL
        )
        sizer.Add(self.minimize_to_tray_on_alt_f4, 0, wx.ALL, 8)

        self.update_check_interval = wx.ComboBox(
            page,
            choices=[UPDATE_CHECK_INTERVAL_LABELS[v] for v in UPDATE_CHECK_INTERVAL_CHOICES]
            + [UPDATE_CHECK_INTERVAL_LABELS[None]],
            style=wx.CB_READONLY,
        )
        self._labelled(page, sizer, f"{UPDATE_CHECK_INTERVAL_LABEL}:", self.update_check_interval)
        self.check_updates_button = wx.Button(page, label="Buscar actualizaciones ahora")
        sizer.Add(self.check_updates_button, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.update_check_status = wx.StaticText(page, label="")
        sizer.Add(self.update_check_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.bridge_updates_panel = wx.Panel(page)
        bridge_box = wx.StaticBoxSizer(
            wx.VERTICAL, self.bridge_updates_panel, "Puente local de WhatsApp"
        )
        self.bridge_version_status = wx.StaticText(self.bridge_updates_panel, label="")
        self.bridge_version_status.SetName("Estado de actualización del puente local")
        self.check_bridge_updates_button = wx.Button(
            self.bridge_updates_panel, label="Buscar actualización del puente"
        )
        self.update_bridge_button = wx.Button(
            self.bridge_updates_panel, label="Actualizar puente ahora"
        )
        self.update_bridge_button.Disable()
        bridge_box.Add(self.bridge_version_status, 0, wx.ALL | wx.EXPAND, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(self.check_bridge_updates_button, 0, wx.RIGHT, 8)
        row.Add(self.update_bridge_button, 0)
        bridge_box.Add(row, 0, wx.ALL, 8)
        self.bridge_updates_panel.SetSizer(bridge_box)
        self.bridge_updates_panel.Hide()
        sizer.Add(self.bridge_updates_panel, 0, wx.ALL | wx.EXPAND, 8)

    def _build_notifications_page(self) -> None:
        page, sizer = self._page(PAGE_TITLES[1])
        self.windows_notifications = wx.CheckBox(page, label=WINDOWS_NOTIFICATIONS_LABEL)
        self.show_preview = wx.CheckBox(page, label=SHOW_PREVIEW_LABEL)
        self.announce_with_nvda = wx.CheckBox(page, label=ANNOUNCE_WITH_NVDA_LABEL)
        self.test_notification_button = wx.Button(page, label="Probar notificación de Windows")
        self.open_chat_sound = wx.CheckBox(page, label=OPEN_CHAT_SOUND_LABEL)
        self.sent_message_sound = wx.CheckBox(page, label=SENT_MESSAGE_SOUND_LABEL)
        sizer.Add(self.windows_notifications, 0, wx.ALL, 8)
        sizer.Add(self.show_preview, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        sizer.Add(self.announce_with_nvda, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        sizer.Add(self.test_notification_button, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        sizer.Add(self.open_chat_sound, 0, wx.ALL, 8)
        sizer.Add(self.sent_message_sound, 0, wx.ALL, 8)
        self.incoming_sound_path = wx.TextCtrl(page, style=wx.TE_READONLY)
        self._labelled(page, sizer, "Sonido para mensajes entrantes:", self.incoming_sound_path)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.choose_incoming_sound_button = wx.Button(page, label="Elegir sonido...")
        self.reset_incoming_sound_button = wx.Button(page, label="Usar predeterminado")
        row.Add(self.choose_incoming_sound_button, 0, wx.RIGHT, 8)
        row.Add(self.reset_incoming_sound_button, 0)
        sizer.Add(row, 0, wx.ALL, 8)

    def _build_messages_page(self) -> None:
        page, sizer = self._page(PAGE_TITLES[2])
        self.emoticon_conversion = wx.CheckBox(page, label=EMOTICON_CONVERSION_LABEL)
        sizer.Add(self.emoticon_conversion, 0, wx.ALL, 8)
        sizer.Add(
            wx.StaticText(
                page,
                label=(
                    "La conversión ocurre al pulsar Espacio después del emoticono, "
                    "o al enviar un mensaje que sólo contiene uno."
                ),
            ),
            0,
            wx.LEFT | wx.RIGHT | wx.BOTTOM,
            8,
        )

    def _build_transcription_page(self) -> None:
        page, sizer = self._page(PAGE_TITLES[3])
        self.transcription_page = page
        self.transcription_status = wx.StaticText(
            page, label="Abre esta pestaña conectado al puente."
        )
        self.transcription_status.SetName("Estado de la transcripción")
        sizer.Add(self.transcription_status, 0, wx.ALL | wx.EXPAND, 8)

        self.transcription_service = wx.CheckBox(
            page, label="Activar el servicio de transcripción de Deepgram"
        )
        sizer.Add(self.transcription_service, 0, wx.ALL, 8)

        self.api_key = wx.TextCtrl(page, style=wx.TE_PASSWORD)
        self._labelled(
            page, sizer, "Nueva clave API de Deepgram (vacía conserva la actual):", self.api_key
        )
        self.clear_api_key = wx.CheckBox(page, label="Borrar la clave propia guardada en el puente")
        sizer.Add(self.clear_api_key, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.transcription_auto_mode = wx.Choice(
            page, choices=[label for _, label in AUTO_MODE_LABELS]
        )
        self._labelled(page, sizer, "Transcripciones automáticas:", self.transcription_auto_mode)

        self.transcription_delivery = wx.RadioBox(
            page,
            label="Entrega de las transcripciones",
            choices=[label for _, label in DELIVERY_LABELS],
            majorDimension=1,
            style=wx.RA_SPECIFY_COLS,
        )
        self.transcription_delivery.SetName("Entrega de las transcripciones")
        sizer.Add(self.transcription_delivery, 0, wx.ALL | wx.EXPAND, 8)

        self.transcription_chat_filter = wx.SearchCtrl(page)
        self.transcription_chat_filter.SetDescriptiveText("Buscar chat")
        self._labelled(page, sizer, "Filtrar chats:", self.transcription_chat_filter)
        # A native list-view with checkboxes: NVDA reads "marcado" per row and Space
        # toggles it (wx.CheckListBox is owner-drawn and does not expose the state).
        self.transcription_chats = wx.ListCtrl(
            page, size=(-1, 180), style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_NO_HEADER
        )
        self.transcription_chats.InsertColumn(0, "Chat", width=520)
        self.transcription_chats.EnableCheckBoxes(True)
        self._labelled(page, sizer, "Chats seleccionados:", self.transcription_chats)
        self.transcription_reload_button = wx.Button(page, label="Volver a cargar desde el puente")
        sizer.Add(self.transcription_reload_button, 0, wx.ALL, 8)
        self._set_transcription_controls_enabled(False)

    def _build_integrations_page(self) -> None:
        page, sizer = self._page(PAGE_TITLES[4])
        self.atajos_api_enabled = wx.CheckBox(page, label="Permitir integración local con Atajos")
        self.atajos_api_enabled.SetName("Permitir integración local con Atajos")
        sizer.Add(self.atajos_api_enabled, 0, wx.ALL, 8)
        sizer.Add(
            wx.StaticText(
                page,
                label=(
                    "Permite buscar contactos y enviar o programar mensajes desde el asistente. "
                    "Sólo escucha en este equipo. El contexto que solicites en Atajos se "
                    "comparte con Gemini. Los programados esperan a que vuelva la conexión."
                ),
            ),
            0,
            wx.LEFT | wx.RIGHT | wx.BOTTOM,
            8,
        )
        self.atajos_api_status = wx.StaticText(page, label="Integración desactivada.")
        sizer.Add(self.atajos_api_status, 0, wx.ALL, 8)

    # -- botones ---------------------------------------------------------------
    def _run_apply(self) -> bool:
        handler = self.apply_handler
        return handler() if handler is not None else True

    def _on_apply(self, _event: wx.CommandEvent) -> None:
        self._run_apply()

    def _on_save(self, _event: wx.CommandEvent) -> None:
        if self._run_apply():
            self.EndModal(wx.ID_OK)

    # -- valores generales -----------------------------------------------------
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
        local_bridge_active: bool = False,
        emoticon_conversion: bool = True,
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
        self.emoticon_conversion.SetValue(emoticon_conversion)
        self.set_update_check_interval_minutes(update_check_interval_minutes)
        self.set_bridge_updates_visible(local_bridge_available and local_bridge_active)
        self.refresh_accessible_states()

    def set_incoming_sound_path(self, path: str) -> None:
        self.incoming_sound_path.SetValue(path or "Sonido predeterminado de WhatsApp CAN")

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
        self.update_check_interval.SetValue(
            UPDATE_CHECK_INTERVAL_LABELS.get(
                interval, UPDATE_CHECK_INTERVAL_LABELS[DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES]
            )
        )

    def set_update_check_runtime_available(self, available: bool) -> None:
        self._update_check_runtime_available = available
        self.update_check_interval.Enable(available)
        self.check_updates_button.Enable(available)
        self.update_check_status.SetLabel(
            "" if available else "Disponible al ejecutar la aplicación instalada."
        )

    def set_update_check_in_progress(self, in_progress: bool) -> None:
        self.check_updates_button.Enable(self._update_check_runtime_available and not in_progress)
        if in_progress:
            self.update_check_status.SetLabel("Buscando actualizaciones en segundo plano...")

    def set_update_check_status(self, status: str) -> None:
        self.update_check_status.SetLabel(status)

    def set_bridge_updates_visible(self, visible: bool) -> None:
        self.bridge_updates_panel.Show(visible)
        if not visible:
            self.bridge_version_status.SetLabel("")
            self.update_bridge_button.Disable()
        self.Layout()

    def set_bridge_update_in_progress(self, in_progress: bool) -> None:
        self._bridge_update_in_progress = in_progress
        self.check_bridge_updates_button.Enable(not in_progress)
        self.update_bridge_button.Enable(False)
        if in_progress:
            self.bridge_version_status.SetLabel("Comprobando el puente local en segundo plano...")
        self.Layout()

    def set_bridge_update_status(self, status: str, *, can_update: bool = False) -> None:
        self.bridge_version_status.SetLabel(status)
        self.check_bridge_updates_button.Enable(not self._bridge_update_in_progress)
        self.update_bridge_button.Enable(can_update and not self._bridge_update_in_progress)
        self.Layout()

    def refresh_accessible_states(self) -> None:
        self._sync_dependent_controls()

    def apply_interactive_state(self) -> None:
        wx.CallAfter(self._sync_dependent_controls)

    def checkbox_state_text(self, checkbox: object) -> str:
        for candidate, base_label in self._checkboxes_with_labels():
            if candidate is checkbox:
                return format_setting_state(base_label, checkbox.GetValue())
        return "Preferencias actualizadas"

    def focus(self) -> None:
        self.notebook.SetFocus()

    def select_page(self, title: str) -> None:
        if title in PAGE_TITLES:
            self.notebook.SetSelection(PAGE_TITLES.index(title))

    def _checkboxes_with_labels(self) -> tuple[tuple[wx.CheckBox, str], ...]:
        return (
            (self.windows_notifications, WINDOWS_NOTIFICATIONS_LABEL),
            (self.show_preview, SHOW_PREVIEW_LABEL),
            (self.announce_with_nvda, ANNOUNCE_WITH_NVDA_LABEL),
            (self.open_chat_sound, OPEN_CHAT_SOUND_LABEL),
            (self.sent_message_sound, SENT_MESSAGE_SOUND_LABEL),
            (self.minimize_to_tray_on_alt_f4, MINIMIZE_TO_TRAY_ON_ALT_F4_LABEL),
            (self.emoticon_conversion, EMOTICON_CONVERSION_LABEL),
        )

    def _on_dependent_toggle(self, event: wx.CommandEvent) -> None:
        self._sync_dependent_controls()
        event.Skip()

    def _sync_dependent_controls(self) -> None:
        enabled = self.windows_notifications.GetValue()
        for control in (self.show_preview, self.announce_with_nvda, self.test_notification_button):
            if control.IsEnabled() != enabled:
                control.Enable(enabled)
        if self._transcription_settings is not None:
            self._set_transcription_controls_enabled(True)

    # -- transcripción ---------------------------------------------------------
    def _set_transcription_controls_enabled(self, loaded: bool) -> None:
        service = loaded and self.transcription_service.GetValue()
        mode = self.transcription_auto_mode_value()
        self.transcription_service.Enable(loaded)
        self.api_key.Enable(loaded and not self.clear_api_key.GetValue())
        self.clear_api_key.Enable(
            loaded
            and self._transcription_settings is not None
            and self._transcription_settings.key_source == "account"
        )
        self.transcription_auto_mode.Enable(service)
        self.transcription_delivery.Enable(service)
        selectable = service and mode in {AUTO_SELECTED, AUTO_ALL_EXCEPT}
        self.transcription_chat_filter.Enable(selectable)
        self.transcription_chats.Enable(selectable)
        self.transcription_reload_button.Enable(True)

    def transcription_auto_mode_value(self) -> str:
        index = self.transcription_auto_mode.GetSelection()
        if index == wx.NOT_FOUND:
            return AUTO_ALL
        return AUTO_MODE_LABELS[index][0]

    def set_transcription_status(self, message: str) -> None:
        self.transcription_status.SetLabel(message)
        self.transcription_status.SetName(f"Estado de la transcripción: {message}")
        self.transcription_page.Layout()

    def set_transcription_loading(self) -> None:
        self._transcription_settings = None
        self._set_transcription_controls_enabled(False)
        self.set_transcription_status("Cargando la configuración desde el puente...")

    def set_transcription_unavailable(self, message: str) -> None:
        self._transcription_settings = None
        self._set_transcription_controls_enabled(False)
        self.set_transcription_status(message)

    def set_transcription_settings(
        self,
        settings: TranscriptionSettings,
        chats: list[tuple[str, str]],
    ) -> None:
        self._transcription_settings = settings
        self.transcription_service.SetValue(settings.service_enabled)
        self.api_key.SetValue("")
        self.clear_api_key.SetValue(False)
        mode = settings.auto_mode
        self.transcription_auto_mode.SetSelection(
            next(i for i, (value, _label) in enumerate(AUTO_MODE_LABELS) if value == mode)
        )
        self.transcription_delivery.SetSelection(
            1 if settings.delivery == DELIVERY_REPLY else 0
        )
        known = {jid.casefold() for jid, _label in chats}
        extra = [(jid, jid) for jid in sorted(settings.selected_chats) if jid not in known]
        self._transcription_chats = [(jid.casefold(), label) for jid, label in chats] + extra
        self._transcription_checked = set(settings.selected_chats)
        self._fill_chat_list()
        self._set_transcription_controls_enabled(True)
        self.set_transcription_status(self._describe_key(settings))

    @staticmethod
    def _describe_key(settings: TranscriptionSettings) -> str:
        if settings.key_source == "account":
            key = f"Clave propia guardada en el puente (termina en {settings.key_hint})."
        elif settings.key_source == "server":
            key = "Se usa la clave configurada en el servidor."
        else:
            key = "No hay clave API: escribe una para usar el servicio."
        state = "activo" if settings.available else "inactivo"
        return f"Servicio {state}. {key}"

    def _fill_chat_list(self) -> None:
        query = self.transcription_chat_filter.GetValue().strip().casefold()
        visible = [
            (jid, label)
            for jid, label in self._transcription_chats
            if not query or query in label.casefold() or query in jid
        ]
        chats = self.transcription_chats
        chats.Freeze()
        self._filling_chats = True
        try:
            chats.DeleteAllItems()
            self._visible_chat_jids = [jid for jid, _label in visible]
            for index, (jid, label) in enumerate(visible):
                chats.InsertItem(index, label)
                chats.CheckItem(index, jid in self._transcription_checked)
        finally:
            self._filling_chats = False
            chats.Thaw()

    def _on_chat_filter(self, _event: wx.CommandEvent) -> None:
        self._fill_chat_list()

    def _on_chat_checked(self, event: wx.ListEvent) -> None:
        if getattr(self, "_filling_chats", False):
            return
        index = event.GetIndex()
        if 0 <= index < len(self._visible_chat_jids):
            jid = self._visible_chat_jids[index]
            if self.transcription_chats.IsItemChecked(index):
                self._transcription_checked.add(jid)
            else:
                self._transcription_checked.discard(jid)

    def _on_transcription_reload(self, _event: wx.CommandEvent) -> None:
        if self.transcription_reload_handler is not None:
            self.transcription_reload_handler()

    def transcription_update(self) -> TranscriptionSettingsUpdate | None:
        """The pending change, None when nothing differs; raises ValueError on bad input."""
        settings = self._transcription_settings
        if settings is None:
            return None
        typed_key = self.api_key.GetValue()
        api_key = validate_api_key(typed_key) if typed_key.strip() else None
        update = TranscriptionSettingsUpdate(
            service_enabled=self.transcription_service.GetValue(),
            auto_mode=self.transcription_auto_mode_value(),
            delivery=DELIVERY_LABELS[max(0, self.transcription_delivery.GetSelection())][0],
            chats=frozenset(self._transcription_checked),
            api_key=api_key,
            clear_api_key=self.clear_api_key.GetValue(),
        )
        unchanged = (
            api_key is None
            and not update.clear_api_key
            and update.service_enabled == settings.service_enabled
            and update.auto_enabled == settings.auto_enabled
            and update.delivery == settings.delivery
            and update.chat_overrides == {
                jid: enabled
                for jid, enabled in settings.chat_overrides.items()
                if enabled is not settings.auto_enabled
            }
        )
        return None if unchanged else update

    def focus_transcription_key(self) -> None:
        self.select_page("Transcripción")
        self.api_key.SetFocus()


# El nombre anterior sigue disponible para los módulos que lo importan.
SettingsPanel = PreferencesDialog
