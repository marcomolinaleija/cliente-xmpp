from __future__ import annotations

import wx

from cliente_xmpp.models.voice import VoiceAudioSettings
from cliente_xmpp.ui.theme import apply_theme


class VoiceCallDialog(wx.Dialog):
    def __init__(self, parent, controller, closed):
        super().__init__(parent, title="Llamada de WhatsApp",
                         size=(560, 560), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.SetMinSize((480, 360))
        self.controller = controller
        self._closed_callback = closed
        self._closing = False
        panel = self.content = wx.ScrolledWindow(self, style=wx.TAB_TRAVERSAL | wx.VSCROLL)
        panel.SetScrollRate(0, 10)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(wx.StaticText(panel, label="Estado de la llamada:"), 0, wx.LEFT | wx.TOP, 8)
        self.status = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY)
        self.status.SetName("Estado de la llamada")
        self.status.SetMinSize((260, 75))
        box.Add(self.status, 0, wx.EXPAND | wx.ALL, 8)
        box.Add(wx.StaticText(panel, label="Contacto de la llamada:"), 0, wx.LEFT, 8)
        self.peer = wx.TextCtrl(panel, style=wx.TE_READONLY)
        self.peer.SetName("Contacto de la llamada")
        box.Add(self.peer, 0, wx.EXPAND | wx.ALL, 8)
        controls = wx.StaticBoxSizer(wx.VERTICAL, panel, "Controles de la llamada")
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.answer = wx.Button(panel, label="Con&testar")
        self.reject = wx.Button(panel, label="&Rechazar")
        self.hangup = wx.Button(panel, label="C&olgar")
        for control, action in ((self.answer, "answer"), (self.reject, "reject")):
            control.Bind(wx.EVT_BUTTON, lambda _event, a=action: controller.action(a))
            row.Add(control, 0, wx.ALL, 3)
        self.hangup.Bind(wx.EVT_BUTTON, lambda _event: controller.finish())
        row.Add(self.hangup, 0, wx.ALL, 3)
        controls.Add(row, 0, wx.ALL, 5)
        self.mute = wx.CheckBox(panel, label="Silenciar &micrófono")
        self.mute.Bind(wx.EVT_CHECKBOX, lambda _e: controller.mute(self.mute.GetValue()))
        controls.Add(self.mute, 0, wx.ALL, 8)
        box.Add(controls, 0, wx.EXPAND | wx.ALL, 8)
        device_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Dispositivos de audio")
        device_box.Add(wx.StaticText(panel, label="&Entrada de audio:"), 0, wx.LEFT | wx.TOP, 8)
        self.input = wx.Choice(panel)
        self.input.SetName("Entrada de audio")
        self.input.SetMinSize((220, -1))
        device_box.Add(self.input, 0, wx.ALL | wx.EXPAND, 8)
        device_box.Add(wx.StaticText(panel, label="Salida de a&udio:"), 0, wx.LEFT, 8)
        self.output = wx.Choice(panel)
        self.output.SetName("Salida de audio")
        self.output.SetMinSize((220, -1))
        device_box.Add(self.output, 0, wx.ALL | wx.EXPAND, 8)
        device_row = wx.BoxSizer(wx.HORIZONTAL)
        self.refresh = wx.Button(panel, label="&Actualizar dispositivos")
        self.apply = wx.Button(panel, label="A&plicar dispositivos")
        self.refresh.Bind(wx.EVT_BUTTON, lambda _e: controller.refresh_devices())
        self.apply.Bind(wx.EVT_BUTTON, self._apply_devices)
        device_row.Add(self.refresh, 0, wx.ALL, 3)
        device_row.Add(self.apply, 0, wx.ALL, 3)
        device_box.Add(device_row, 0, wx.ALL, 5)
        box.Add(device_box, 0, wx.EXPAND | wx.ALL, 8)
        self.diagnostics = wx.Button(panel, label="Copiar &diagnóstico de audio")
        self.diagnostics.Bind(wx.EVT_BUTTON, lambda _e: controller.request_diagnostics())
        box.Add(self.diagnostics, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        box.Add(wx.StaticText(panel, label="Usa auriculares para evitar realimentación."),
                0, wx.LEFT | wx.RIGHT, 8)
        close = self.close_button = wx.Button(panel, label="&Cerrar (Escape)")
        close.Bind(wx.EVT_BUTTON, lambda _e: self.Close())
        box.Add(close, 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        panel.SetSizer(box)
        panel.FitInside()
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(outer)
        self.Bind(wx.EVT_CLOSE, self._close)
        self.Bind(wx.EVT_CHAR_HOOK, self._key)
        self._device_rows = None
        self._input_ids, self._output_ids = [], []
        self._input_keys, self._output_keys = [], []
        apply_theme(self)
        # Foreground/background colouring makes MSW owner-draw the checkbox as a
        # pushbutton, hiding its checked state from MSAA/NVDA. Restore native style.
        self.mute.SetForegroundColour(wx.NullColour)
        self.mute.SetBackgroundColour(wx.NullColour)
        self.render()

    def _apply_devices(self, _event):
        self.controller.configure(VoiceAudioSettings(
            self._input_ids[self.input.GetSelection()],
            self._output_ids[self.output.GetSelection()], self.mute.GetValue(),
            self._input_keys[self.input.GetSelection()][1],
            self._output_keys[self.output.GetSelection()][1],
        ))

    def _key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Close()
        else:
            event.Skip()

    def _close(self, _event):
        if self._closing:
            return
        self._closing = True
        focus = wx.Window.FindFocus()
        self.had_focus_when_closed = bool(focus and (focus is self or self.IsDescendant(focus)))
        self.controller.finish()
        self._closed_callback(self)
        self.Destroy()

    def render(self):
        if self._closing:
            return
        c = self.controller
        audio = ("Micrófono silenciado" if c.settings.muted else "Audio activo")
        status = c.status + "\n" + (audio if c.audio_running else "Micrófono cerrado")
        self._text(self.status, status)
        peer = c.caller_name or c.target_name
        self._text(self.peer, peer)
        title = "Llamada con " + (peer[:80] or "contacto de WhatsApp")
        if self.GetTitle() != title:
            self.SetTitle(title)
        incoming = c.state == "incoming_ringing"
        self.answer.Show(incoming)
        self.reject.Show(incoming)
        self.hangup.Show(not incoming)
        self.answer.Enable(c.connected and c.state == "incoming_ringing" and not c.pending)
        self.reject.Enable(c.connected and c.state == "incoming_ringing" and not c.pending)
        self.hangup.Enable(c.busy and c.pending not in {"reject", "hangup"})
        self.mute.SetValue(c.settings.muted)
        self.refresh.Enable(c.connected or c.load_local_devices is not None)
        self.apply.Enable(not c.pending)
        self.diagnostics.Enable(c.connected)
        if self._device_rows != c.devices:
            self._device_rows = c.devices
            self._choices(self.input, True, c.settings.input_device)
            self._choices(self.output, False, c.settings.output_device)
        self.Layout()
        self.content.FitInside()

    @staticmethod
    def _text(control, value):
        if control.GetValue() == value:
            return
        start, end = control.GetSelection()
        control.ChangeValue(value)
        limit = control.GetLastPosition()
        control.SetSelection(min(start, limit), min(end, limit))

    def _choices(self, control, input_device, selected):
        old_ids = self._input_ids if input_device else self._output_ids
        old_keys = self._input_keys if input_device else self._output_keys
        identity = (self.controller.settings.input_identity if input_device
                    else self.controller.settings.output_identity)
        if old_ids and control.GetSelection() >= 0:
            selected = old_ids[control.GetSelection()]
            identity = old_keys[control.GetSelection()][1]
        rows = [d for d in self.controller.devices if d.input] if input_device else [
            d for d in self.controller.devices if d.output
        ]
        ids = [None] + [d.index for d in rows]
        keys = [(None, "")] + [(d.index, d.identity) for d in rows]
        names = ["Predeterminado de Windows"] + [d.name for d in rows]
        selected_key = (selected, identity)
        if selected_key not in keys:
            ids.append(selected)
            keys.append(selected_key)
            names.append("Dispositivo seleccionado no disponible")
        control.Freeze()
        try:
            control.Set(names)
            control.SetSelection(keys.index(selected_key))
        finally:
            control.Thaw()
        if input_device:
            self._input_ids = ids
            self._input_keys = keys
        else:
            self._output_ids = ids
            self._output_keys = keys
