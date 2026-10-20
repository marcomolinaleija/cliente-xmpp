"""Call window state and user actions. All methods run on the wx thread.

There is no history/store integration: live revisions never mark a chat unread.
"""
from __future__ import annotations

import uuid

from cliente_xmpp.models.voice import VoiceAudioSettings
from cliente_xmpp.xmpp.events import (
    VoiceActionFailed,
    VoiceAudioChanged,
    VoiceChannelChanged,
    VoiceDevicesLoaded,
    VoiceDiagnostics,
    VoiceStateChanged,
)

STATE_LABELS = {
    "idle": "Sin llamada", "initiating": "Iniciando llamada", "ringing": "Llamando",
    "incoming_ringing": "Llamada entrante", "connecting": "Conectando llamada",
    "active": "Llamada en curso", "ended": "Llamada terminada",
}


class VoiceController:
    def __init__(self, service, account, component, render, announce, incoming, name_for_jid,
                 available=lambda: True, before_call=lambda: True, ringback=None):
        self.service = service
        self.account, self.component = account, component
        self.render, self.announce, self.incoming = render, announce, incoming
        self.name_for_jid = name_for_jid
        self.available, self.before_call = available, before_call
        self.ringback = ringback
        self._ringback_cancelled = False
        self._feedback_armed = False
        self._connected_feedback = False
        self._feedback_call_id = ""
        self.load_local_devices = None
        self.session_id = ""
        self.connected = False
        self.connecting = False
        self.state = "idle"
        self.revision = -1
        self.call_id = ""
        self.chat_jid = ""
        self.target = ""
        self.target_name = ""
        self.caller_name = ""
        self.settings = VoiceAudioSettings()
        self.devices = ()
        self.pending = ""
        self.action_id = ""
        self.queued_dial = False
        self.audio_running = False
        self.diagnostics_request = ""
        self.diagnostics_ready = lambda _stats: None
        self.detail = "Llamadas desactivadas"

    @property
    def busy(self):
        return (self.queued_dial or self.state not in {"idle", "ended"}
                or self.pending in {"dial", "answer"})

    def request_call(self, jid, name):
        """Only called after the contact-specific confirmation on the wx thread."""
        if self.busy or not self.available():
            return False
        self.select(jid, name)
        if not self.target:
            return False
        self.caller_name = self.target_name
        self.diagnostics_request = ""
        self.queued_dial = True
        if self.connected:
            self._dial_confirmed()
        else:
            self.enable()
        self.render()
        return True

    def _dial_confirmed(self):
        if not self.queued_dial:
            return
        self.queued_dial = False
        if self.state in {"idle", "ended"}:
            self.action("dial")

    def finish(self):
        """End this call/window; keep listening for future incoming calls."""
        self._stop_ringback()
        self.queued_dial = False
        if self.pending in {"hangup", "reject"}:
            return
        if self.pending in {"dial", "answer"} or (self.call_id and not self.connected):
            # Cancel a device check/acceptance even before a call ID or ready channel.
            self.disable()
            if self.available():
                self.enable()
        elif self.state == "incoming_ringing":
            self.action("reject")
        elif self.call_id and self.state not in {"idle", "ended"}:
            self.action("hangup")
        else:
            self.detail = "Llamada cancelada" if self.connecting else "Sin llamada"
        self.render()

    @property
    def status(self):
        return self.detail or STATE_LABELS.get(self.state, "Sin llamada")

    def select(self, jid, name):
        if self.busy:
            return
        bare = jid.split("/", 1)[0]
        if "/" in jid or "@" not in bare or bare.rsplit("@", 1)[1] != self.component:
            self.target = self.target_name = ""
        else:
            self.target, self.target_name = bare, name[:120]
        self.render()

    def enable(self):
        if self.session_id:
            return
        if not self.available():
            self.detail = "Conecta WhatsApp antes de activar las llamadas."
            self.announce(self.detail)
            self.render()
            return
        self.session_id = uuid.uuid4().hex
        self.connecting = True
        self.detail = "Conectando llamadas de voz…"
        self.service.start_voice(self.session_id)
        self.render()

    def disable(self):
        self._stop_ringback()
        self._end_feedback()
        old, self.session_id = self.session_id, ""
        self.connecting = self.connected = self.audio_running = False
        self.state, self.call_id, self.pending = "idle", "", ""
        self.action_id = ""
        self.queued_dial = False
        self.revision = -1
        self.diagnostics_request = ""
        self.detail = "Llamadas desactivadas"
        if old:
            self.service.stop_voice(old)
        self.render()

    def action(self, action):
        if not self.connected:
            return
        if action == "dial" and (self.busy or not self.target):
            return
        if action in {"answer", "reject"} and self.state != "incoming_ringing":
            return
        if action == "hangup" and (not self.call_id or self.state in {"idle", "ended"}):
            return
        if self.pending and action != "hangup":
            return
        if action in {"dial", "answer"} and not self.before_call():
            return
        if action in {"dial", "answer"}:
            self._feedback_armed = True
            self._connected_feedback = False
            self._feedback_call_id = self.call_id if action == "answer" else ""
        if action == "dial":
            self._ringback_cancelled = False
        elif action in {"reject", "hangup"}:
            self._stop_ringback()
        self.pending = action
        self.action_id = uuid.uuid4().hex
        if action == "dial":
            self.caller_name = self.target_name
        self.detail = {"dial": "Iniciando llamada…", "answer": "Contestando…",
                       "reject": "Rechazando…", "hangup": "Terminando llamada…"}[action]
        self.service.voice_command(
            action, call_id=self.call_id if action != "dial" else "",
            peer=self.target if action == "dial" else "", session_id=self.session_id,
            audio_settings=self.settings if action in {"dial", "answer"} else None,
            request_id=self.action_id,
        )
        self.render()

    def mute(self, value):
        from dataclasses import replace
        self.settings = replace(self.settings, muted=value)
        if self.session_id:
            self.service.voice_audio(self.session_id, muted=value)
        self.announce("Micrófono silenciado" if value else "Micrófono habilitado")
        self.render()

    def configure(self, settings):
        self.settings = settings
        if self.connected:
            self.service.voice_audio(self.session_id, settings=settings)

    def refresh_devices(self):
        if self.connected:
            self.service.voice_audio(self.session_id, load_devices=True)
        elif self.load_local_devices:
            self.load_local_devices()

    def request_diagnostics(self):
        if self.connected:
            self.diagnostics_request = uuid.uuid4().hex
            self.service.voice_audio(self.session_id, diagnostics_request=self.diagnostics_request)

    def local_devices(self, devices, error=""):
        self.devices = devices
        if error:
            self.detail = error
        self.render()

    def _stop_ringback(self):
        self._ringback_cancelled = True
        if self.ringback is not None:
            self.ringback.stop()

    def _end_feedback(self):
        if self._feedback_armed:
            self._feedback_armed = False
            if self.ringback is not None:
                self.ringback.ended()

    def event(self, event):
        if (
            not self.session_id or event.session_id != self.session_id
            or event.account_jid != self.account
            or getattr(event, "component_jid", self.component) != self.component
        ):
            return
        if isinstance(event, VoiceChannelChanged):
            self.connecting = False
            self.connected = event.connected
            if not event.connected:
                self._stop_ringback()
                self._end_feedback()
                self.audio_running = False
                self.pending = ""
                self.queued_dial = False
                self.state, self.call_id = "idle", ""
                self.detail = event.detail or "Se cerró el canal de llamadas"
                self.session_id = ""
            else:
                self.detail = STATE_LABELS[self.state]
                self.refresh_devices()
                self._dial_confirmed()
        elif isinstance(event, VoiceStateChanged):
            if event.revision <= self.revision:
                return
            previous, previous_id = self.state, self.call_id
            if (self._feedback_call_id and event.call_id
                    and self._feedback_call_id != event.call_id
                    or event.direction == "incoming" and self.pending == "dial"):
                self._stop_ringback()
                self._end_feedback()
            if self._feedback_armed and event.call_id:
                self._feedback_call_id = event.call_id
            if previous_id != event.call_id:
                self.diagnostics_request = ""
            if event.state not in {"idle", "ended"}:
                self.queued_dial = False
            if (
                previous_id and event.call_id and previous_id != event.call_id
                or (event.direction == "incoming" and self.pending == "dial")
            ):
                self.action_id = ""
            self.state, self.call_id, self.revision = event.state, event.call_id, event.revision
            if self.ringback is not None:
                if (event.direction == "outgoing" and event.state in {"initiating", "ringing"}
                        and not self._ringback_cancelled):
                    self.ringback.start()
                else:
                    self.ringback.stop()
            if event.state == "active" and self._feedback_armed and not self._connected_feedback:
                self._connected_feedback = True
                if self.ringback is not None:
                    self.ringback.connected()
            elif event.state == "ended":
                self._end_feedback()
            self.chat_jid = event.chat_jid
            if event.chat_jid:
                self.caller_name = self.name_for_jid(event.chat_jid)[:120]
            elif event.direction == "incoming":
                self.caller_name = "Contacto de WhatsApp"
            if previous != event.state or previous_id != event.call_id:
                self.pending = ""
                self.detail = STATE_LABELS[event.state]
                if event.state == "incoming_ringing":
                    self.incoming(event.chat_jid, self.caller_name)
                elif event.state in {"active", "ended"}:
                    self.announce(STATE_LABELS[event.state])
            if event.state != "active":
                self.audio_running = False
        elif isinstance(event, VoiceAudioChanged):
            self.audio_running = event.running
            if event.detail:
                self.detail = event.detail
                self.announce(event.detail)
        elif isinstance(event, VoiceDiagnostics):
            if self.diagnostics_request and event.request_id == self.diagnostics_request:
                self.diagnostics_request = ""
                self.diagnostics_ready(event.stats)
        elif isinstance(event, VoiceDevicesLoaded):
            self.devices = event.devices
            if event.error:
                self.detail = event.error
        elif isinstance(event, VoiceActionFailed):
            if event.request_id and event.request_id != self.action_id:
                return
            if event.call_id and event.call_id != self.call_id:
                return
            self._stop_ringback()
            if event.action == "dial":
                self._end_feedback()
            self.pending = ""
            self.detail = event.detail
            self.announce(event.detail)
        self.render()
