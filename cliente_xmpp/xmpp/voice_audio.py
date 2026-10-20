"""Async ownership of native voice devices; no wx or XMPP XML."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from cliente_xmpp.audio.live_call import CAPTURE_FRAMES, LiveAudioError, LiveCallAudio, devices
from cliente_xmpp.audio.voice_timing import AudioCadence
from cliente_xmpp.models.voice import VoiceAudioSettings
from cliente_xmpp.xmpp.events import VoiceAudioChanged, VoiceDevicesLoaded


class VoiceAudioSession:
    def __init__(self, transport, factory=LiveCallAudio):
        self.transport = transport
        self.native = factory()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="call-audio")
        self.settings = VoiceAudioSettings()
        self.call_id = ""
        self.outgoing = False
        self.baseline = -1
        self.desired = False
        self.running = False
        self.version = 0
        self.sync_task: asyncio.Task | None = None
        self.pump_task: asyncio.Task | None = None
        self.closed = False
        self.mute_version = 0
        self._close_task: asyncio.Task | None = None

    def emit(self, detail=""):
        t = self.transport
        t.emit(VoiceAudioChanged(t.account, t.component, t.session_id,
                                self.running, self.settings.muted, detail))

    async def check(self, settings):
        if self.closed:
            raise LiveAudioError("El canal de audio está cerrado.")
        await asyncio.get_running_loop().run_in_executor(self.worker, self.native.check, settings)

    async def load_devices(self):
        t = self.transport
        try:
            found = await asyncio.get_running_loop().run_in_executor(self.worker, devices)
            t.emit(VoiceDevicesLoaded(t.account, t.session_id, found))
        except LiveAudioError as exc:
            t.emit(VoiceDevicesLoaded(t.account, t.session_id, error=str(exc)))

    def latest_mute(self, settings, version):
        return (replace(settings, muted=self.settings.muted)
                if version != self.mute_version else settings)

    def authorize(self, call_id, outgoing, settings):
        self.settings = settings
        self.call_id, self.outgoing = call_id, outgoing
        self.baseline = self.transport.revision
        self.native.mute(settings.muted)

    def state(self, call_id, direction, state, revision):
        if (
            self.outgoing and not self.call_id and direction == "outgoing"
            and call_id and revision > self.baseline and state not in {"idle", "ended"}
        ):
            self.call_id = call_id
        wanted = state == "active" and bool(self.call_id) and call_id == self.call_id
        if state in {"idle", "ended"}:
            self.call_id, self.outgoing = "", False
        self._desire(wanted)

    def revoke(self):
        self.call_id, self.outgoing = "", False
        self._desire(False)

    def mute(self, value):
        self.mute_version += 1
        self.settings = replace(self.settings, muted=value)
        self.native.mute(value)
        self.emit()

    async def configure(self, settings):
        mute_version = self.mute_version
        await self.check(settings)
        if self.closed:
            return
        settings = self.latest_mute(settings, mute_version)
        self.settings = settings
        self.native.mute(settings.muted)
        if self.desired:
            self.native.invalidate()
            self.running = False
            self.version += 1
            self._schedule()

    def _desire(self, wanted):
        if self.closed or wanted == self.desired:
            return
        self.desired = wanted
        self.version += 1
        if not wanted:
            self.native.invalidate()
            self.running = False
        self._schedule()

    def _schedule(self):
        if not self.sync_task or self.sync_task.done():
            self.sync_task = asyncio.create_task(self._sync())

    async def _sync(self):
        loop = asyncio.get_running_loop()
        handled = -1
        while not self.closed and handled != self.version:
            handled = self.version
            try:
                await loop.run_in_executor(self.worker, self.native.stop)
            except LiveAudioError as exc:
                self.native.invalidate()
                self.desired = self.running = False
                self.emit(str(exc))
                self.transport.audio_failed(self.call_id)
                return
            if self.desired:
                permit = self.native.permit(self.settings)
                try:
                    started = await loop.run_in_executor(
                        self.worker, self.native.start, permit, self.settings,
                    )
                except LiveAudioError as exc:
                    self.native.invalidate()
                    self.desired = self.running = False
                    self.emit(str(exc))
                    self.transport.audio_failed(self.call_id)
                    return
                if self.closed or handled != self.version:
                    self.native.invalidate()
                    continue
                self.running = started
                if started and (not self.pump_task or self.pump_task.done()):
                    self.pump_task = asyncio.create_task(self._pump())
            self.emit()

    async def _pump(self):
        cadence = AudioCadence()
        while not self.closed:
            late_ms = round(await cadence.wait() * 1000)
            if self.closed:
                return
            if self.running and late_ms >= 20:
                self.transport.pump_late_ticks += 1
                self.transport.pump_late_max_ms = max(self.transport.pump_late_max_ms, late_ms)
            if self.running:
                failure = self.native.failure
                if failure:
                    call_id = self.call_id
                    self.revoke()
                    self.emit(failure)
                    self.transport.audio_failed(call_id)
                    return
                for _ in range(CAPTURE_FRAMES):
                    data = self.native.take_capture()
                    if data is None:
                        break
                    self.transport.feed(data)
                for _ in range(3):
                    try:
                        data = self.transport.playback.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    self.native.play(data)

    async def close(self):
        self.closed = True
        self.desired = self.running = False
        self.native.invalidate()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_native())
        await asyncio.shield(self._close_task)

    async def _close_native(self):
        if self.pump_task:
            self.pump_task.cancel()
            await asyncio.gather(self.pump_task, return_exceptions=True)
        try:
            if self.sync_task:
                await asyncio.shield(self.sync_task)
            await asyncio.get_running_loop().run_in_executor(self.worker, self.native.stop)
        except LiveAudioError:
            pass
        finally:
            self.worker.shutdown(wait=False, cancel_futures=False)
            self.emit()
