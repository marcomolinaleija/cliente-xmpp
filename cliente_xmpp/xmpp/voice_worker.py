"""Keep duplex PCM off the XMPP service loop; only commands/events cross threads."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import uuid
from types import SimpleNamespace

from cliente_xmpp.audio.live_call import LiveCallAudio
from cliente_xmpp.xmpp.events import VoiceStateChanged
from cliente_xmpp.xmpp.voice import VoiceTransport, request_voice_grant


class VoiceMediaLoop:
    def __init__(self):
        self.ready = concurrent.futures.Future()
        self.thread = threading.Thread(target=self._run, name="can-client-voice", daemon=True)
        self.thread.start()

    def _run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        except BaseException as exc:
            self.ready.set_exception(exc)
            return
        self.ready.set_result(loop)
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()

    async def call(self, coroutine):
        try:
            loop = await asyncio.shield(asyncio.wrap_future(self.ready))
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except BaseException:
            coroutine.close()
            raise
        return await asyncio.wrap_future(future)

    async def stop(self):
        loop = await asyncio.shield(asyncio.wrap_future(self.ready))
        loop.call_soon_threadsafe(loop.stop)
        await asyncio.to_thread(self.thread.join, 5)
        if self.thread.is_alive():
            raise RuntimeError("Voice media worker did not stop")


class IsolatedVoiceTransport:
    """Owner-loop facade. Never expose media queues or aiohttp objects to XMPP."""

    def __init__(self, client, component, emit, *, session_id="", audio_factory=LiveCallAudio):
        self.client, self.component, self.emit = client, component, emit
        self.account = str(client.boundjid.bare)
        self.session_id = session_id or uuid.uuid4().hex
        self.audio_factory = audio_factory
        self.call_id = ""
        self.worker = self.transport = None
        self.closed = False
        self._close_task = None

    async def open(self):
        if self.closed or self.worker:
            raise ValueError("El canal de voz ya está abierto o cerrado.")
        grant = await request_voice_grant(self.client, self.component)
        if self.closed:
            raise ValueError("Se cerró el canal de voz.")
        owner = asyncio.get_running_loop()
        # Immutable scalar snapshot: the worker must never use Slixmpp objects.
        snapshot = SimpleNamespace(boundjid=SimpleNamespace(bare=self.account),
                                   settings=SimpleNamespace(
                                       use_tls=self.client.settings.use_tls,
                                       ca_file=self.client.settings.ca_file))

        def deliver(event):
            if isinstance(event, VoiceStateChanged):
                self.call_id = event.call_id
            self.emit(event)

        def emit(event):
            if not owner.is_closed():
                owner.call_soon_threadsafe(deliver, event)

        async def start():
            self.transport = VoiceTransport(snapshot, self.component, emit,
                                            session_id=self.session_id,
                                            audio_factory=self.audio_factory,
                                            authorized_grant=grant)
            await self.transport.open()

        self.worker = VoiceMediaLoop()
        try:
            await self.worker.call(start())
        except BaseException:
            await self.close()
            raise

    async def command(self, action, **kwargs):
        if self.closed or not self.transport:
            raise ValueError("El canal de voz no está disponible.")
        await self.worker.call(self.transport.command(action, **kwargs))

    async def configure_audio(self, **kwargs):
        if self.closed or not self.transport:
            raise ValueError("El canal de voz no está disponible.")
        await self.worker.call(self.transport.configure_audio(**kwargs))

    async def diagnostics(self):
        if self.closed or not self.transport:
            raise ValueError("El canal de voz no está disponible.")
        return await self.worker.call(self.transport.diagnostics())

    async def close(self):
        self.closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        if self.worker is None:
            return

        async def close_transport():
            if self.transport:
                await self.transport.close()

        try:
            await self.worker.call(close_transport())
        finally:
            await self.worker.stop()
