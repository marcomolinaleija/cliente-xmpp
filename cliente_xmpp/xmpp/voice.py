"""Authenticated live voice transport; all operations belong to one asyncio loop.

PCM queues are bounded. No media packet enters wx events, message history or SQLite.
Microphone/device ownership belongs to the audio layer, not this protocol module.
"""
from __future__ import annotations

import asyncio
import json
import ssl
import uuid
from dataclasses import replace
from socket import gaierror
from time import perf_counter
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from aiohttp import (
    ClientConnectorError,
    ClientSession,
    ClientTimeout,
    TCPConnector,
    ThreadedResolver,
    TraceConfig,
    WSMsgType,
)
from slixmpp import JID

from cliente_xmpp.audio.live_call import CAPTURE_FRAMES, SILENCE, LiveCallAudio
from cliente_xmpp.audio.voice_timing import AudioCadence
from cliente_xmpp.models.voice import VoiceAudioSettings, VoiceAudioStats
from cliente_xmpp.xmpp.events import VoiceChannelChanged, VoiceStateChanged
from cliente_xmpp.xmpp.voice_audio import VoiceAudioSession

NAMESPACE = "urn:can:whatsapp:voice:1"
PROTOCOL = "can-voice-1"
STATES = {"idle", "initiating", "ringing", "incoming_ringing", "connecting", "active", "ended"}


def validate_grant(result, component: str) -> tuple[str, str]:
    if str(result["from"]) != component:
        raise ValueError("La autorización procede de otro puente.")
    node = result.xml.find(f"{{{NAMESPACE}}}open")
    if node is None or any(node.get(k) != v for k, v in {
        "protocol": PROTOCOL, "format": "pcm16le", "rate": "16000", "channels": "1",
    }.items()):
        raise ValueError("El puente no ofrece un formato de voz compatible.")
    url, token = node.get("url", ""), node.get("token", "")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "wss" or not parsed.hostname or parsed.username or parsed.password
        or parsed.query or parsed.fragment or parsed.path != "/voice/v1" or len(url) > 2048
        or not 32 <= len(token) <= 128 or not token.isascii()
        or not all(char.isalnum() or char in "-_" for char in token)
    ):
        raise ValueError("La autorización de voz no es válida.")
    _ = parsed.port
    if not 0 < int(node.get("expires-in", "0")) <= 30:
        raise ValueError("La autorización de voz caducó.")
    return url, token


def latest(queue: asyncio.Queue, data: bytes) -> bool:
    dropped = queue.full()
    if dropped:
        queue.get_nowait()
    queue.put_nowait(data)
    return dropped


async def request_voice_grant(client, component):
    """Authorization belongs to the XMPP owner loop, not the media worker."""
    if not client.settings.use_tls:
        raise ValueError("La voz requiere una conexión XMPP con TLS.")
    iq = client.Iq()
    iq["type"], iq["to"] = "get", component
    iq.append(ET.Element(f"{{{NAMESPACE}}}open"))
    return validate_grant(await iq.send(timeout=10), component)


class VoiceTransport:
    def __init__(self, client, component: str, emit, *, session_id="", audio_factory=LiveCallAudio,
                 authorized_grant=None):
        self.client = client
        self.component = component
        self.account = str(client.boundjid.bare)
        self.emit = emit
        self.session_id = session_id or uuid.uuid4().hex
        self.audio_factory = audio_factory
        self.authorized_grant = authorized_grant
        self.audio: VoiceAudioSession | None = None
        self.call_id = ""
        self.capture: asyncio.Queue[bytes] = asyncio.Queue(CAPTURE_FRAMES)
        self.playback: asyncio.Queue[bytes] = asyncio.Queue(3)
        self.pending: dict[str, asyncio.Future] = {}
        self.task: asyncio.Task | None = None
        self._close_task: asyncio.Task | None = None
        self.ws = None
        self.revision = -1
        self.state = "idle"
        self.capture_dropped = self.playback_dropped = 0
        self.receive_gap_max_ms = self.send_max_ms = 0
        self.pump_late_ticks = self.pump_late_max_ms = 0
        self._last_receive = None

    async def open(self) -> None:
        if self.task:
            raise ValueError("El canal de voz ya está abierto.")
        ready = asyncio.get_running_loop().create_future()
        self.task = asyncio.create_task(self._run(ready))
        try:
            await ready
        except BaseException:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            raise

    async def _run(self, ready: asyncio.Future) -> None:
        detail = ""
        try:
            url, token = (self.authorized_grant if self.authorized_grant is not None
                          else await request_voice_grant(self.client, self.component))
            self.authorized_grant = None
            tls = ssl.create_default_context(cafile=self.client.settings.ca_file or None)
            tls.minimum_version = ssl.TLSVersion.TLSv1_2
            trace = TraceConfig()

            async def reject_redirect(*_args):
                raise ValueError("El canal de voz no admite redirecciones.")

            trace.on_request_redirect.append(reject_redirect)
            async with ClientSession(
                # Use the system DNS configuration, also when aiodns is installed.
                # getaddrinfo runs in the loop's executor, never on the wx thread.
                connector=TCPConnector(resolver=ThreadedResolver()),
                timeout=ClientTimeout(total=None, sock_connect=5),
                trace_configs=[trace], trust_env=False,
            ) as http:
                async with asyncio.timeout(10):
                    ws = await http.ws_connect(
                        url, headers={"Authorization": "Bearer " + token},
                        protocols=(PROTOCOL,), ssl=tls, heartbeat=15, max_msg_size=4096,
                    )
                async with ws:
                    if ws.protocol != PROTOCOL:
                        raise ValueError("El puente no confirmó el protocolo de voz.")
                    self.ws = ws
                    initial = await ws.receive(timeout=5)
                    if initial.type != WSMsgType.TEXT:
                        raise ValueError("El puente no envió el estado inicial de voz.")
                    self._message(initial.data)
                    if self.revision < 0:
                        raise ValueError("El puente no envió el estado inicial de voz.")
                    self.emit(VoiceChannelChanged(self.account, self.component, True,
                                                  session_id=self.session_id))
                    ready.set_result(None)
                    sender = asyncio.create_task(self._send_audio())
                    try:
                        async for message in ws:
                            if message.type == WSMsgType.BINARY:
                                if not 0 < len(message.data) <= 3200 or len(message.data) % 2:
                                    raise ValueError("Paquete de audio no válido.")
                                if self.state == "active":
                                    now = perf_counter()
                                    if self._last_receive is not None:
                                        self.receive_gap_max_ms = max(
                                            self.receive_gap_max_ms,
                                            round((now - self._last_receive) * 1000),
                                        )
                                    self._last_receive = now
                                    self.playback_dropped += latest(self.playback, message.data)
                            elif message.type == WSMsgType.TEXT:
                                self._message(message.data)
                            else:
                                break
                    finally:
                        sender.cancel()
                        await asyncio.gather(sender, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            detail = "No se pudo mantener el canal de voz seguro. Revisa la conexión del puente."
            dns_failure = (isinstance(exc, ClientConnectorError)
                           and isinstance(exc.os_error, gaierror))
            if dns_failure:
                detail = "No se pudo resolver la dirección del puente de voz (DNS)."
            if not ready.done():
                ready.set_exception(ValueError(
                    detail if dns_failure else "No se pudo abrir el canal de voz seguro.",
                ))
        finally:
            if not ready.done():
                ready.cancel()
            self.ws = None
            self.state = "idle"
            if self.audio:
                await self.audio.close()
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ValueError("Se cerró el canal de voz."))
            self.pending.clear()
            for queue in (self.capture, self.playback):
                while not queue.empty():
                    queue.get_nowait()
            self.emit(VoiceChannelChanged(self.account, self.component, False, detail,
                                          session_id=self.session_id))

    def _message(self, text: str) -> None:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Invalid voice event")
        if data.get("type") == "result":
            future = self.pending.get(data.get("id"))
            if future and not future.done():
                if data.get("error"):
                    future.set_exception(ValueError("El puente rechazó la acción de llamada."))
                else:
                    future.set_result(None)
            return
        revision = data.get("revision")
        chat_jid = data.get("chat_jid", "")
        if not isinstance(chat_jid, str) or len(chat_jid) > 1024:
            raise ValueError("Invalid voice contact")
        if chat_jid and (
            JID(chat_jid).domain != self.component or JID(chat_jid).resource
            or not JID(chat_jid).node
        ):
            raise ValueError("Foreign voice contact")
        if (
            data.get("type") != "state" or data.get("state") not in STATES
            or type(revision) is not int or revision < 0
            or any(not isinstance(data.get(key, ""), str) or len(data.get(key, "")) > limit
                   for key, limit in (("call_id", 128), ("peer", 256), ("reason", 128)))
            or data.get("direction", "") not in ("", "incoming", "outgoing")
        ):
            raise ValueError("Invalid voice state")
        if revision <= self.revision:
            return
        self.revision = revision
        self.state = data["state"]
        self.call_id = data.get("call_id", "")
        if self.audio:
            self.audio.state(self.call_id, data.get("direction", ""), self.state, revision)
        if self.state != "active":
            self._last_receive = None
            for queue in (self.capture, self.playback):
                while not queue.empty():
                    queue.get_nowait()
        self.emit(VoiceStateChanged(
            self.account, self.component, data.get("call_id", ""), data.get("peer", ""),
            data.get("direction", ""), self.state, data.get("reason", ""), revision,
            self.session_id, data.get("chat_jid", ""),
        ))

    def _audio(self):
        if self.audio is None:
            self.audio = VoiceAudioSession(self, self.audio_factory)
        return self.audio

    def audio_failed(self, call_id):
        async def end():
            try:
                await self.command("hangup", call_id=call_id)
            except (ValueError, TimeoutError):
                pass
        if call_id and call_id == self.call_id:
            asyncio.create_task(end())

    async def command(
        self, action: str, *, call_id: str = "", peer: str = "",
        audio_settings: VoiceAudioSettings | None = None,
    ) -> None:
        if self.audio and action in {"hangup", "reject"} and call_id == self.call_id:
            self.audio.revoke()
        if self.ws is None or action not in {"dial", "answer", "reject", "hangup"}:
            raise ValueError("El canal de voz no está disponible.")
        if len(self.pending) >= 8:
            raise ValueError("Hay demasiadas acciones de llamada pendientes.")
        armed = False
        if audio_settings is not None and action in {"dial", "answer"}:
            audio = self._audio()
            mute_version = audio.mute_version
            await audio.check(audio_settings)
            if self.ws is None or (
                action == "answer" and (call_id != self.call_id or self.state != "incoming_ringing")
            ) or (action == "dial" and self.state not in {"idle", "ended"}):
                raise ValueError("La llamada cambió antes de abrir el audio.")
            audio.authorize(
                call_id, action == "dial", audio.latest_mute(audio_settings, mute_version),
            )
            self.capture_dropped = self.playback_dropped = 0
            self.receive_gap_max_ms = self.send_max_ms = 0
            self.pump_late_ticks = self.pump_late_max_ms = 0
            self._last_receive = None
            armed = True
        identity = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        try:
            try:
                await self.ws.send_json({"id": identity, "action": action,
                                         "call_id": call_id, "peer": peer})
            except (OSError, RuntimeError):
                raise ValueError("Se cerró el canal de voz.") from None
            async with asyncio.timeout(12):
                await future
        except BaseException:
            if armed:
                self.audio.revoke()
            raise
        finally:
            self.pending.pop(identity, None)
            if not future.done():
                future.cancel()

    def feed(self, data: bytes) -> None:
        if len(data) != 640:
            raise ValueError("Se requieren 320 muestras PCM16 mono.")
        if self.state == "active" and self.ws is not None:
            self.capture_dropped += latest(self.capture, bytes(data))

    async def _send_audio(self) -> None:
        cadence = AudioCadence()
        while self.ws is not None:
            data = await self.capture.get()
            await cadence.wait()
            # Recheck authorization/mute after the pacing wait, before any write.
            if self.state == "active" and self.ws is not None:
                if self.audio:
                    if not self.audio.running:
                        continue
                    if self.audio.settings.muted:
                        data = SILENCE
                started = perf_counter()
                await self.ws.send_bytes(data)
                self.send_max_ms = max(self.send_max_ms, round((perf_counter() - started) * 1000))

    def statistics(self):
        native = self.audio.native.statistics() if self.audio else VoiceAudioStats()
        return replace(
            native, transport_capture_dropped=self.capture_dropped,
            transport_playback_dropped=self.playback_dropped,
            receive_gap_max_ms=self.receive_gap_max_ms, send_max_ms=self.send_max_ms,
            pump_late_ticks=self.pump_late_ticks, pump_late_max_ms=self.pump_late_max_ms,
        )

    async def diagnostics(self):
        return self.statistics()

    async def configure_audio(self, *, settings=None, muted=None, load_devices=False):
        audio = self._audio()
        if load_devices:
            await audio.load_devices()
        if settings is not None:
            await audio.configure(settings)
        if muted is not None:
            audio.mute(muted)
            while not self.capture.empty():
                self.capture.get_nowait()

    async def close(self) -> None:
        if self.audio:
            self.audio.revoke()
            self.audio.running = False
            self.audio.native.invalidate()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_once())
        await asyncio.shield(self._close_task)

    async def _close_once(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self.audio:
            await self.audio.close()
