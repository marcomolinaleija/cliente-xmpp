"""Main-window hooks for modeless voice controls; wx thread only."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import wx

from cliente_xmpp.audio.live_call import devices
from cliente_xmpp.audio.ringback import RingbackSound
from cliente_xmpp.ui.voice_controller import VoiceController
from cliente_xmpp.ui.voice_dialog import VoiceCallDialog
from cliente_xmpp.xmpp.events import (
    VoiceActionFailed,
    VoiceAudioChanged,
    VoiceChannelChanged,
    VoiceDevicesLoaded,
    VoiceDiagnostics,
    VoiceStateChanged,
    XmppDisconnected,
)


class VoiceIntegrationMixin:
    def _ensure_voice_controller(self):
        account = self.current_jid.split("/", 1)[0]
        c = getattr(self, "_voice_controller", None)
        if c is not None and (c.account, c.component) == (account, self.whatsapp_component_jid):
            return c
        self._close_voice()
        c = VoiceController(
            self.xmpp, account, self.whatsapp_component_jid,
            self._render_voice, self.speaker.speak, self._voice_incoming,
            self._display_name_for_jid, available=self._voice_available,
            before_call=self._voice_before_call,
            ringback=RingbackSound(),
        )
        self._voice_controller = c
        c.load_local_devices = self._load_voice_devices
        c.diagnostics_ready = self._voice_diagnostics_ready
        return c

    def _voice_diagnostics_ready(self, stats):
        from cliente_xmpp.ui.voice_diagnostics import diagnostic_text
        copied = False
        if wx.TheClipboard.Open():
            try:
                copied = bool(wx.TheClipboard.SetData(wx.TextDataObject(diagnostic_text(stats))))
            finally:
                wx.TheClipboard.Close()
        if copied:
            message = "Diagnóstico de audio copiado."
        else:
            message = "No se pudo abrir el portapapeles. Intenta copiar el diagnóstico otra vez."
        self.status_bar.SetStatusText(message)
        self.speaker.speak(message)

    def _sync_voice_connection(self, enabled):
        # This hook is used only by initialized windows, after WhatsApp verification.
        if not hasattr(self, "_voice_controller"):
            return
        self.conversation.set_voice_component(self.whatsapp_component_jid if enabled else "")
        if enabled and self.current_jid and self.whatsapp_component_jid:
            self._ensure_voice_controller().enable()
        else:
            c = self._voice_controller
            if c is not None:
                c.disable()

    def _voice_chat_supported(self, chat):
        component = getattr(self, "whatsapp_component_jid", "")
        return bool(chat and not chat.is_group and component and "/" not in chat.jid
                    and "@" in chat.jid and chat.jid.rsplit("@", 1)[1] == component)

    def _on_call_contact(self, _event=None):
        self._call_voice_contact(self.conversation.current_chat)

    def _call_voice_contact(self, chat):
        if not self._voice_chat_supported(chat) or not self.whatsapp_verified:
            return
        c = self._ensure_voice_controller()
        if c.busy:
            self._show_voice_dialog()
            return
        account, component = c.account, c.component
        jid = chat.jid
        name = (chat.custom_name or chat.name or self._display_name_for_jid(jid))[:120]
        answer = wx.MessageBox(
            f"Estás a punto de llamar a {name}.\n¿Quieres continuar?",
            "Confirmar llamada", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, parent=self,
        )
        if answer != wx.YES:
            return
        # Modal dispatch may have changed the profile, connection or incoming call.
        if (c is not self._voice_controller or account != self.current_jid.split("/", 1)[0]
                or component != self.whatsapp_component_jid or not self.whatsapp_verified):
            return
        if c.request_call(jid, name):
            self._show_voice_dialog()
        elif c.busy:
            self._show_voice_dialog()

    def _show_voice_dialog(self, *, activate=True):
        dialog = getattr(self, "_voice_dialog", None)
        if dialog is not None and not dialog.IsBeingDeleted():
            if activate:
                dialog.Show()
                dialog.Raise()
                dialog.mute.SetFocus()
            return dialog
        controller = self._voice_controller
        dialog = self._voice_dialog = VoiceCallDialog(
            self, controller, self._voice_dialog_closed,
        )
        controller.refresh_devices()
        if activate:
            dialog.Show()
            dialog.mute.SetFocus()
        else:
            dialog.ShowWithoutActivating()
        return dialog

    def _load_voice_devices(self):
        c = self._voice_controller
        executor = getattr(self, "_voice_device_executor", None)
        if executor is None:
            executor = self._voice_device_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="voice-devices",
            )

        def worker():
            try:
                found, error = devices(), ""
            except RuntimeError:
                found, error = (), "No se pudieron consultar los dispositivos de audio."

            def apply():
                if (
                    not getattr(self, "_closing", False)
                    and getattr(self, "_voice_controller", None) is c
                    and getattr(self, "_voice_dialog", None) is not None
                ):
                    c.local_devices(found, error)

            wx.CallAfter(apply)

        executor.submit(worker)

    def _shutdown_voice_devices(self):
        executor = getattr(self, "_voice_device_executor", None)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
            self._voice_device_executor = None

    def _voice_available(self):
        c = getattr(self, "_voice_controller", None)
        if not self.whatsapp_verified or not self.whatsapp_component_jid or c is None:
            return False
        if c.account != self.current_jid.split("/", 1)[0]:
            return False
        return c.component == self.whatsapp_component_jid

    def _voice_before_call(self):
        if self.audio_recorder.is_recording:
            message = "Termina o cancela la nota de voz antes de iniciar una llamada."
            self.status_bar.SetStatusText(message)
            self.speaker.speak(message)
            return False
        self.conversation.close_audio()
        return True

    def _voice_incoming(self, chat_jid, name):
        # Existing call-history notifications already implement chat mute, toast,
        # sound and NVDA fallback. Live control must not duplicate those alerts.
        self.status_bar.SetStatusText("Llamada entrante de " + name[:120])
        muted = getattr(self, "_chat_notifications_muted", lambda _jid: False)(chat_jid)
        if not muted:
            self._show_voice_dialog(activate=False)

    def _render_voice(self):
        dialog = getattr(self, "_voice_dialog", None)
        if dialog is not None and not dialog.IsBeingDeleted():
            dialog.render()

    def _voice_dialog_closed(self, dialog):
        if getattr(self, "_voice_dialog", None) is dialog:
            self._voice_dialog = None
        if (getattr(dialog, "had_focus_when_closed", False)
                and not getattr(self, "_closing", False) and self.IsShown()):
            wx.CallAfter(self.conversation.messages.SetFocus)

    def _close_voice(self):
        c = getattr(self, "_voice_controller", None)
        if c is not None:
            c.disable()
        dialog = getattr(self, "_voice_dialog", None)
        if dialog is not None and not dialog.IsBeingDeleted():
            dialog.Close()

    def _handle_voice_event(self, event):
        c = getattr(self, "_voice_controller", None)
        if isinstance(event, XmppDisconnected):
            if c is not None:
                c.disable()
            return False
        if isinstance(event, (VoiceChannelChanged, VoiceStateChanged, VoiceAudioChanged,
                              VoiceDevicesLoaded, VoiceDiagnostics, VoiceActionFailed)):
            if c is not None:
                c.event(event)
            return True
        return False
