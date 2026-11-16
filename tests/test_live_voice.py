from __future__ import annotations

import asyncio
import ctypes
import json
import math
import struct
import threading
import unittest
import wave
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx
from slixmpp import JID
from voice_fakes import AudioBackend

from cliente_xmpp.audio.live_call import SILENCE, LiveAudioError, LiveCallAudio
from cliente_xmpp.audio.ringback import RingbackSound
from cliente_xmpp.audio.voice_playout import PcmPlayout
from cliente_xmpp.audio.voice_timing import AudioCadence
from cliente_xmpp.models.chat import Chat
from cliente_xmpp.models.voice import VoiceAudioSettings, VoiceAudioStats, VoiceDevice
from cliente_xmpp.ui.conversation_panel import ConversationPanel
from cliente_xmpp.ui.voice_controller import VoiceController
from cliente_xmpp.ui.voice_dialog import VoiceCallDialog
from cliente_xmpp.ui.voice_integration import VoiceIntegrationMixin
from cliente_xmpp.xmpp.events import (
    VoiceActionFailed,
    VoiceChannelChanged,
    VoiceDevicesLoaded,
    VoiceDiagnostics,
    VoiceStateChanged,
)
from cliente_xmpp.xmpp.voice import VoiceTransport
from tools.probe_voice_windows import accessible

ACCOUNT = "fixture@example.invalid"
COMPONENT = "whatsapp.example.invalid"
PEER = "fixture@" + COMPONENT


class LiveAudioTests(unittest.TestCase):
    def test_device_reindex_cannot_silently_capture_another_microphone(self):
        backend = AudioBackend()
        backend.query_devices = Mock(return_value={"name": "Different microphone", "hostapi": 0})
        backend.query_hostapis = Mock(return_value={"name": "Fixture driver"})
        audio = LiveCallAudio(backend, clock=backend.clock)
        settings = VoiceAudioSettings(input_device=1,
                                      input_identity="Fixture driver\0Chosen microphone")
        token = audio.permit(settings)
        with self.assertRaises(LiveAudioError):
            audio.start(token, settings)
        self.assertFalse(backend.streams)

    def test_pcm_fragmentation_overflow_mute_and_closed_callback(self):
        backend = AudioBackend()
        audio = LiveCallAudio(backend, clock=backend.clock)
        self.assertFalse(backend.streams)
        permit = audio.permit(VoiceAudioSettings())
        self.assertTrue(audio.start(permit, VoiceAudioSettings()))
        stream = backend.streams[0]
        received = bytes(range(128)) * 15
        audio.play(received)
        backend.advance(.06)
        output = b"".join(stream.frame(bytes([i])*640) for i in range(3))
        self.assertEqual(output, received)
        for i in range(10):
            stream.frame(bytes([i])*640)
        self.assertEqual(audio.capture.qsize(), 6)
        self.assertEqual(audio.take_capture(), bytes([4])*640)
        audio.mute(True)
        self.assertIsNone(audio.take_capture())
        stream.frame(bytes([127])*640)
        self.assertEqual(audio.take_capture(), SILENCE)
        audio.invalidate()
        self.assertEqual(stream.frame(bytes([127])*640), SILENCE)
        self.assertIsNone(audio.take_capture())
        audio.stop()
        self.assertTrue(stream.closed)
        self.assertFalse(audio.failure)

    def test_ending_during_native_open_never_starts_obsolete_microphone(self):
        backend = AudioBackend()
        backend.block_open = threading.Event()
        audio = LiveCallAudio(backend, clock=backend.clock)
        token = audio.permit(VoiceAudioSettings())
        result = []
        worker = threading.Thread(
            target=lambda: result.append(audio.start(token, VoiceAudioSettings())),
        )
        worker.start()
        self.assertTrue(backend.opening.wait(1))
        audio.invalidate()
        backend.block_open.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [False])
        self.assertFalse(backend.streams[0].started)
        self.assertTrue(backend.streams[0].closed)

    def test_capture_survives_100ms_service_pause_and_counts_longer_overflow(self):
        backend = AudioBackend()
        audio = LiveCallAudio(backend, clock=backend.clock)
        settings = VoiceAudioSettings()
        token = audio.permit(settings)
        audio.start(token, settings)
        try:
            for i in range(5):
                backend.streams[0].frame(bytes([i+1])*640)
            self.assertEqual(audio.statistics().capture_dropped, 0)
            self.assertEqual([audio.take_capture() for _ in range(5)],
                             [bytes([i+1])*640 for i in range(5)])
            for i in range(8):
                backend.streams[0].frame(bytes([i+1])*640)
            self.assertEqual(audio.statistics().capture_dropped, 2)
            self.assertEqual(audio.take_capture(), bytes([3])*640)
            with audio._lock:
                self.assertEqual(backend.streams[0].frame(bytes([127])*640), SILENCE)
            self.assertEqual(audio.statistics().callback_lock_misses, 1)
            flags = SimpleNamespace(input_overflow=True, output_underflow=True)
            audio._callback(token, SILENCE, bytearray(640), 320, None, flags)
            self.assertEqual(audio.statistics().driver_input_overflows, 1)
            self.assertEqual(audio.statistics().driver_output_underflows, 1)
        finally:
            audio.invalidate()
            audio.stop()


class PlayoutTests(unittest.TestCase):
    def test_variable_delivery_without_packet_loss_has_no_gaps_or_reordered_pcm(self):
        backend = AudioBackend()
        audio = LiveCallAudio(backend, clock=backend.clock)
        settings = VoiceAudioSettings()
        audio.start(audio.permit(settings), settings)
        arrivals = [(i*60+[0, 40, 0, 20, 0][i % 5], struct.pack("<h", i+1)*960)
                    for i in range(100)]
        outputs, position = [], 0
        try:
            for ms in range(0, 6400, 20):
                backend.now = ms / 1000
                while position < len(arrivals) and arrivals[position][0] <= ms:
                    audio.play(arrivals[position][1])
                    position += 1
                outputs.append(backend.streams[0].frame(SILENCE))
            first = next(i for i, data in enumerate(outputs) if data != SILENCE)
            last = max(i for i, data in enumerate(outputs) if data != SILENCE)
            played = b"".join(outputs[first:last+1])
            self.assertEqual(first*20, 60)
            self.assertEqual(played, b"".join(data for _, data in arrivals))
            self.assertEqual(audio.statistics().playback_dropped_ms, 0)
        finally:
            audio.invalidate()
            audio.stop()

    def test_packet_sizes_partial_frames_and_reserve_remain_bounded(self):
        p = PcmPlayout()
        # A packet is not necessarily one PortAudio block.
        data = bytes(range(128))*30
        for start, size in ((0, 320), (320, 640), (960, 1920), (2880, 960)):
            p.push(data[start:start+size], 0)
        self.assertEqual(p.pop(640, .04), b"")
        self.assertEqual(b"".join(p.pop(640, .06+i*.02) for i in range(6)), data)
        self.assertEqual(p.pop(640, .18), b"")
        self.assertEqual(p.gaps, 1)
        self.assertEqual(p.prefill_ms, 80)
        for i in range(12):
            when = 1 + i
            p.push(bytes([i+1])*1920, when)
            self.assertEqual(p.pop(640, when+.04), b"")
            for j in range(4):
                p.pop(640, when+.12+j*.02)
        self.assertEqual(p.prefill_ms, 120)
        for i in range(50):
            p.push(bytes([i+1])*3200, 20)
        self.assertLessEqual(len(p.data), 240*32)
        self.assertGreater(p.dropped_bytes, 0)
        p.clear()
        self.assertEqual(p.pop(640, 30), b"")
        self.assertTrue(p.empty())


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.005)


class AudioCadenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_coarse_wakeup_does_not_slow_pcm_or_accumulate_small_delays(self):
        class Clock:
            def __init__(self, quantum, overhead):
                self.quantum, self.overhead = quantum, overhead
                self.now = 0.
                self.sleeps = []

            def time(self):
                return self.now

            async def sleep(self, delay):
                self.sleeps.append(delay)
                self.now += math.ceil(delay / self.quantum) * self.quantum + self.overhead

        for quantum, overhead in ((.015625, 0), (.001, .002)):
            with self.subTest(quantum=quantum, overhead=overhead):
                clock = Clock(quantum, overhead)
                cadence = AudioCadence(clock=clock.time, sleep=clock.sleep)
                stamps = []
                for _ in range(501):
                    await cadence.wait()
                    stamps.append(clock.now)
                self.assertGreaterEqual(stamps[-1], 10.)
                self.assertLess(stamps[-1], 10.02)
                self.assertTrue(all(s >= .001 for s in clock.sleeps))
                self.assertLess(len(clock.sleeps), 1001)

    async def test_early_wakeup_is_rechecked_and_stall_never_causes_catchup_burst(self):
        now = 0.
        early = True

        async def sleep(delay):
            nonlocal now, early
            now += delay / 2 if early else delay
            early = False

        cadence = AudioCadence(clock=lambda: now, sleep=sleep)
        await cadence.wait()
        await cadence.wait()
        self.assertAlmostEqual(now, .02)
        now += .2
        self.assertAlmostEqual(await cadence.wait(), .18)
        after_stall = now
        await cadence.wait()
        self.assertAlmostEqual(now - after_stall, .02)


class LiveAudioTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_capture_is_paced_and_mute_is_rechecked_after_wait(self):
        for operation in ("mute", "close"):
            with self.subTest(operation=operation):
                t = self.transport()
                t.state = "active"
                t._audio().running = True
                sent, times = [], []

                async def send(data, sent=sent, times=times):
                    times.append(perf_counter())
                    sent.append(data)

                t.ws.send_bytes = send
                for i in range(4):
                    t.feed(bytes([i+1])*640)
                sender = asyncio.create_task(t._send_audio())
                try:
                    await until(lambda sent=sent: len(sent) == 1)
                    if operation == "close":
                        await t.close()
                        await asyncio.sleep(.08)
                        self.assertEqual(len(sent), 1)
                    else:
                        t.audio.mute(True)
                        await until(lambda sent=sent: len(sent) == 4)
                        self.assertEqual(sent[0], bytes([1])*640)
                        self.assertEqual(sent[1:], [SILENCE]*3)
                        self.assertTrue(all(
                            b-a >= .007 for a, b in zip(times, times[1:], strict=False)
                        ))
                        self.assertGreaterEqual(times[-1] - times[0], .06)
                finally:
                    sender.cancel()
                    await asyncio.gather(sender, return_exceptions=True)
                    await t.close()

    async def test_mute_while_device_check_is_pending_cannot_be_overwritten_by_dial(self):
        t = self.transport()
        entered, release = threading.Event(), threading.Event()

        def check(**_kwargs):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("fixture check timeout")

        self.backend.check_input_settings = check
        dial = asyncio.create_task(
            t.command("dial", peer=PEER, audio_settings=VoiceAudioSettings()),
        )
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            t.audio.mute(True)
        finally:
            release.set()
        await dial
        await until(lambda: t.audio.running)
        self.assertTrue(t.audio.settings.muted)
        self.backend.streams[0].frame(bytes([127])*640)
        self.assertEqual(t.audio.native.take_capture(), SILENCE)

    def transport(self):
        self.backend = AudioBackend()
        self.events = []
        client = SimpleNamespace(boundjid=JID(ACCOUNT))
        self.t = VoiceTransport(client, COMPONENT, self.events.append,
                                audio_factory=lambda: LiveCallAudio(
                                    self.backend, clock=self.backend.clock,
                                ))
        self.t.revision = 0
        self.t.ws = SimpleNamespace(send_json=self.command, send_bytes=self.send_audio)
        self.sent = []
        self.commands = []
        return self.t

    async def command(self, command):
        self.commands.append(command)
        if command["action"] == "dial":
            self.state("active", "outgoing", "fixture-call")
        elif command["action"] == "answer":
            self.state("active", "incoming", command["call_id"])
        elif command["action"] == "hangup":
            self.state("ended", "incoming", command["call_id"])
        self.t._message(json.dumps({"type": "result", "id": command["id"]}))

    async def send_audio(self, data):
        self.sent.append(data)
        await self.t.playback.put(data)

    def state(self, state, direction, call_id):
        self.t._message(json.dumps({"type": "state", "revision": self.t.revision+1,
                                    "state": state, "direction": direction,
                                    "call_id": call_id, "peer": "fixture@lid"}))

    async def asyncTearDown(self):
        if hasattr(self, "t"):
            await self.t.close()

    async def test_explicit_answer_opens_native_audio_and_ends_on_driver_loss(self):
        t = self.transport()
        t._audio()  # Device configuration alone does not authorize capture.
        self.state("incoming_ringing", "incoming", "fixture-call")
        self.assertFalse(self.backend.streams)
        self.state("active", "incoming", "fixture-call")
        await asyncio.sleep(.02)
        self.assertFalse(self.backend.streams)
        self.state("incoming_ringing", "incoming", "fixture-call")
        await t.command("answer", call_id="fixture-call", audio_settings=VoiceAudioSettings())
        await until(lambda: t.audio.running)
        self.backend.streams[0].kwargs["finished_callback"]()
        await until(lambda: any(c["action"] == "hangup" for c in self.commands))
        await until(lambda: self.backend.streams[0].closed)
        self.assertFalse(t.audio.running)

    async def test_duplex_pcm_and_mute_filter_queued_capture_before_network(self):
        t = self.transport()
        await t.command("dial", peer=PEER, audio_settings=VoiceAudioSettings())
        await until(lambda: t.audio.running)
        sender = asyncio.create_task(t._send_audio())
        try:
            pcm = bytes(range(128))*5
            self.backend.streams[0].frame(pcm)
            await until(lambda: bool(self.sent))
            await until(lambda: not t.audio.native.playback.empty())
            self.backend.advance(.06)
            self.assertEqual(self.backend.streams[0].frame(SILENCE), pcm)
            t.audio.mute(True)
            t.feed(pcm)
            await until(lambda: len(self.sent)>1)
            self.assertEqual(self.sent[-1], SILENCE)
            await t.command("hangup", call_id="fixture-call")
            await until(lambda: self.backend.streams[0].closed)
            self.assertFalse(t.audio.running)
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)


class VoiceControllerTests(unittest.TestCase):
    def test_connect_and_end_cues_once_for_explicit_calls_not_passive_incoming(self):
        for direction in ("outgoing", "incoming"):
            with self.subTest(direction=direction):
                c = self.controller()
                sound = c.ringback = Mock()
                if direction == "outgoing":
                    c.request_call(PEER, "Fixture contact")
                else:
                    c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid",
                                             direction, "incoming_ringing", "", 1,
                                             self.session, PEER))
                    c.action("answer")
                for revision in (2, 3):
                    c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid",
                                             direction, "active", "", revision, self.session, PEER))
                sound.connected.assert_called_once()
                sound.ended.assert_not_called()
                c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", direction,
                                         "ended", "", 50, "old-session", PEER))
                sound.ended.assert_not_called()
                for revision in (4, 5):
                    c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", direction,
                                             "ended", "", revision, self.session, PEER))
                c.event(VoiceChannelChanged(ACCOUNT, COMPONENT, False, session_id=self.session))
                c.disable()
                sound.ended.assert_called_once()
        c = self.controller()
        sound = c.ringback = Mock()
        for revision, state in ((1, "ended"), (2, "incoming_ringing"), (3, "ended")):
            c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "passive-call", "fixture@lid", "incoming",
                                     state, "", revision, self.session, PEER))
        sound.connected.assert_not_called()
        sound.ended.assert_not_called()

    def test_end_cue_on_cancel_or_disconnect_and_new_attempt_rearms_feedback(self):
        for ending in ("cancel", "disconnect", "disable", "dial_error"):
            with self.subTest(ending=ending):
                c = self.controller()
                sound = c.ringback = Mock()
                c.request_call(PEER, "Fixture contact")
                if ending == "cancel":
                    c.finish()
                elif ending == "disconnect":
                    c.event(VoiceChannelChanged(ACCOUNT, COMPONENT, False,
                                               session_id=self.session))
                elif ending == "disable":
                    c.disable()
                else:
                    c.event(VoiceActionFailed(ACCOUNT, COMPONENT, self.session, "dial", "",
                                             "Fixture failure", c.action_id))
                c.disable()
                sound.connected.assert_not_called()
                sound.ended.assert_called_once()
                c.enable()
                self.session = c.session_id
                c.event(VoiceChannelChanged(ACCOUNT, COMPONENT, True, session_id=self.session))
                c.request_call(PEER, "Fixture contact")
                c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "new-call", "fixture@lid", "outgoing",
                                         "active", "", 1, self.session, PEER))
                c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "new-call", "fixture@lid", "outgoing",
                                         "ended", "", 2, self.session, PEER))
                sound.connected.assert_called_once()
                self.assertEqual(sound.ended.call_count, 2)

    def test_connection_and_end_assets_are_brief_non_looping_and_replace_ringback(self):
        backend = SimpleNamespace(PlaySound=Mock(), SND_FILENAME=1, SND_ASYNC=2,
                                  SND_LOOP=4, SND_NODEFAULT=8)
        sound = RingbackSound(backend)
        for cue, filename, frames in ((sound.connected, "call-connected.wav", 4160),
                                      (sound.ended, "call-ended.wav", 4160)):
            with self.subTest(filename=filename):
                sound.start()
                backend.PlaySound.reset_mock()
                cue()
                calls = backend.PlaySound.call_args_list
                self.assertEqual(calls[0].args, (None, 0))
                self.assertEqual(calls[1].args[1], 11)  # Asynchronous, without SND_LOOP.
                self.assertTrue(calls[1].args[0].endswith(filename))
                sound.stop()
                self.assertEqual(backend.PlaySound.call_count, 2)
                with wave.open(calls[1].args[0], "rb") as wav:
                    self.assertEqual(wav.getnframes(), frames)
                    self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()),
                                     (1, 2, 16000))
                    self.assertTrue(any(wav.readframes(frames)))
        with wave.open(str(sound.path.with_name("call-connected.wav")), "rb") as wav:
            connected = wav.readframes(wav.getnframes())
        with wave.open(str(sound.path.with_name("call-ended.wav")), "rb") as wav:
            ended = wav.readframes(wav.getnframes())
        # Preserve both original notes exactly; change only their order.
        self.assertEqual(ended, connected[4800:] + connected[3520:4800] + connected[:3520])

    def test_ringback_only_outgoing_and_stops_on_call_lifecycle_or_local_cancel(self):
        for ending in ("connecting", "active", "ended", "incoming_ringing", "finish",
                       "disable", "disconnect", "error"):
            with self.subTest(ending=ending):
                c = self.controller()
                sound = c.ringback = Mock()
                c.request_call(PEER, "Fixture contact")
                sound.start.assert_not_called()
                c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "outgoing",
                                         "initiating", "", 1, self.session, PEER))
                sound.start.assert_called_once()
                sound.reset_mock()
                # Neither a stale revision nor another session can stop the tone.
                for revision, sid in ((1, self.session), (50, "old-session")):
                    c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid",
                                             "outgoing", "ended", "", revision, sid, PEER))
                sound.stop.assert_not_called()
                c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "outgoing",
                                         "ringing", "", 2, self.session, PEER))
                sound.reset_mock()
                if ending == "finish":
                    c.finish()
                elif ending == "disable":
                    c.disable()
                elif ending == "disconnect":
                    c.event(VoiceChannelChanged(ACCOUNT, COMPONENT, False,
                                               session_id=self.session))
                elif ending == "error":
                    c.event(VoiceActionFailed(ACCOUNT, COMPONENT, self.session, "dial", "call",
                                             "Fixture failure", c.action_id))
                else:
                    direction = "incoming" if ending == "incoming_ringing" else "outgoing"
                    c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", direction,
                                             ending, "", 3, self.session, PEER))
                sound.stop.assert_called()
                sound.start.assert_not_called()
                if ending == "finish":
                    # A later ringing revision cannot revive sound after local hangup.
                    c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid",
                                             "outgoing", "ringing", "", 4, self.session, PEER))
                    sound.start.assert_not_called()

    def test_ringback_backend_is_async_idempotent_and_asset_contains_cadenced_tone(self):
        backend = SimpleNamespace(PlaySound=Mock(), SND_FILENAME=1, SND_ASYNC=2,
                                  SND_LOOP=4, SND_NODEFAULT=8)
        sound = RingbackSound(backend)
        sound.stop()
        backend.PlaySound.assert_not_called()
        with wave.open(str(sound.path), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()),
                             (1, 2, 16000))
            self.assertEqual(wav.getnframes(), 48000)
            pcm = wav.readframes(wav.getnframes())
        self.assertTrue(any(pcm[:28800]))
        self.assertFalse(any(pcm[28800:]))
        sound.start()
        sound.start()
        backend.PlaySound.assert_called_once_with(str(sound.path), 15)
        sound.stop()
        sound.stop()
        self.assertEqual(backend.PlaySound.call_count, 2)
        backend.PlaySound.assert_called_with(None, 0)
        backend.PlaySound.side_effect = RuntimeError("Fixture unavailable device")
        sound.start()
        self.assertFalse(sound.playing)
        sound.stop()

    def test_diagnostics_are_requested_explicitly_and_correlated_without_identity_in_text(self):
        from cliente_xmpp.ui.voice_diagnostics import diagnostic_text
        c = self.controller()
        copied = c.diagnostics_ready = Mock()
        c.request_diagnostics()
        request = c.diagnostics_request
        stats = VoiceAudioStats(capture_dropped=2, playback_gaps=1)
        c.event(VoiceDiagnostics(ACCOUNT, COMPONENT, self.session, "old", stats))
        copied.assert_not_called()
        c.event(VoiceDiagnostics(ACCOUNT, COMPONENT, self.session, request, stats))
        copied.assert_called_once_with(stats)
        text = diagnostic_text(stats)
        self.assertIn("2", text)
        self.assertNotIn(ACCOUNT, text)
        self.assertNotIn(PEER, text)
    def test_closing_while_answer_checks_audio_revokes_old_intent_and_keeps_listening(self):
        c = self.controller()
        sid = c.session_id
        c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "incoming",
                                 "incoming_ringing", "", 1, sid, PEER))
        c.action("answer")
        self.assertEqual(c.pending, "answer")
        c.finish()
        self.service.stop_voice.assert_called_once_with(sid)
        self.assertNotEqual(c.session_id, sid)
        c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "incoming",
                                 "active", "", 2, sid, PEER))
        self.assertEqual(c.state, "idle")
        self.assertFalse(c.audio_running)

    def test_listener_never_dials_and_every_incoming_opens_foreground_even_if_muted(self):
        c = VoiceController(Mock(), ACCOUNT, COMPONENT, Mock(), Mock(), Mock(), lambda _: "Fixture")
        window = SimpleNamespace(
            _voice_controller=c, current_jid=ACCOUNT, whatsapp_component_jid=COMPONENT,
            conversation=SimpleNamespace(set_voice_component=Mock()),
            _ensure_voice_controller=Mock(return_value=c),
            _chat_notifications_muted=Mock(return_value=False),
            _show_voice_dialog=Mock(), status_bar=Mock(),
            IsIconized=Mock(return_value=False), Iconize=Mock(),
            IsShown=Mock(return_value=True), Show=Mock(),
        )
        VoiceIntegrationMixin._sync_voice_connection(window, True)
        VoiceIntegrationMixin._sync_voice_connection(window, True)
        c.service.start_voice.assert_called_once()
        c.service.voice_command.assert_not_called()
        window._show_voice_dialog.assert_not_called()
        VoiceIntegrationMixin._voice_incoming(window, PEER, "Fixture")
        window._show_voice_dialog.assert_called_once_with(activate=True)
        window._show_voice_dialog.reset_mock()
        window._chat_notifications_muted.return_value = True
        window.IsIconized.return_value = True
        window.IsShown.return_value = False
        window._tray_hidden = True
        VoiceIntegrationMixin._voice_incoming(window, PEER, "Fixture")
        window._show_voice_dialog.assert_called_once_with(activate=True)
        window.Iconize.assert_called_once_with(False)
        window.Show.assert_called_once()
        self.assertFalse(window._tray_hidden)
        sid = c.session_id
        VoiceIntegrationMixin._sync_voice_connection(window, False)
        c.service.stop_voice.assert_called_once_with(sid)

    def test_confirmed_call_waits_for_channel_once_and_cancel_or_incoming_clears_intent(self):
        for action in ("connect", "cancel", "incoming", "disconnect"):
            with self.subTest(action=action):
                service = Mock()
                c = VoiceController(service, ACCOUNT, COMPONENT, Mock(), Mock(), Mock(),
                                    lambda _: "Fixture")
                self.assertTrue(c.request_call(PEER, "Confirmed contact"))
                self.assertTrue(c.busy)
                sid = c.session_id
                service.voice_command.assert_not_called()
                c.select("other@" + COMPONENT, "Other contact")
                self.assertEqual(c.target, PEER)
                if action == "cancel":
                    c.finish()
                elif action == "incoming":
                    c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "incoming", "fixture@lid",
                                             "incoming", "incoming_ringing", "", 1, sid, PEER))
                elif action == "disconnect":
                    c.disable()
                c.event(VoiceChannelChanged(ACCOUNT, COMPONENT, True, session_id=sid))
                c.event(VoiceChannelChanged(ACCOUNT, COMPONENT, True, session_id=sid))
                if action == "connect":
                    service.voice_command.assert_called_once()
                    self.assertEqual(service.voice_command.call_args.kwargs["peer"], PEER)
                else:
                    service.voice_command.assert_not_called()

    def controller(self):
        self.service, self.render, self.announce, self.incoming = Mock(), Mock(), Mock(), Mock()
        c = VoiceController(self.service, ACCOUNT, COMPONENT, self.render, self.announce,
                            self.incoming, lambda jid: "Fixture contact")
        c.enable()
        self.session = c.session_id
        c.event(VoiceChannelChanged(ACCOUNT, COMPONENT, True, session_id=self.session))
        return c

    def test_incoming_actions_are_explicit_and_stale_sessions_cannot_end_new_call(self):
        c = self.controller()
        c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "incoming",
                                 "incoming_ringing", "", 1, self.session, PEER))
        self.service.voice_command.assert_not_called()
        self.incoming.assert_called_once_with(PEER, "Fixture contact")
        c.action("answer")
        self.assertEqual(self.service.voice_command.call_args.args, ("answer",))
        self.assertIsInstance(self.service.voice_command.call_args.kwargs["audio_settings"],
                              VoiceAudioSettings)
        c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "incoming",
                                 "active", "", 2, "old-session"))
        self.assertEqual(c.state, "incoming_ringing")
        c.event(VoiceActionFailed(ACCOUNT, COMPONENT, self.session, "answer", "call", "fixture"))
        self.assertFalse(c.pending)
        c.disable()
        self.service.stop_voice.assert_called_with(self.session)
        c.event(VoiceChannelChanged(ACCOUNT, COMPONENT, True, session_id=self.session))
        self.assertFalse(c.connected)

    def test_duration_starts_on_active_not_ringing_and_survives_duplicate_active(self):
        c = self.controller()
        now = [10.]
        c.clock = lambda: now[0]
        c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "outgoing",
                                 "ringing", "", 1, self.session, PEER))
        now[0] = 20.
        self.assertEqual(c.duration, "00:00")
        c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "outgoing",
                                 "active", "", 2, self.session, PEER))
        now[0] = 95.
        c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "outgoing",
                                 "active", "", 3, self.session, PEER))
        self.assertEqual(c.duration, "01:15")
        now[0] = 3621.
        self.assertEqual(c.duration, "1:00:01")
        c.disable()
        self.assertEqual(c.duration, "00:00")


class VoiceDialogNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App.Get() or wx.App(False)

    def call_window(self):
        class Window(VoiceIntegrationMixin, wx.Frame):
            pass

        window = Window(None, title="Fixture chat")
        window._closing = False
        window._voice_dialog = None
        window.status_bar = Mock()
        window.conversation = SimpleNamespace(
            messages=wx.TextCtrl(window, value="Fixture message", style=wx.TE_MULTILINE),
            composer=wx.TextCtrl(window),
        )
        window._voice_controller = VoiceController(
            Mock(), ACCOUNT, COMPONENT, window._render_voice, Mock(), window._voice_incoming,
            lambda _: "Fixture contact",
        )
        c = window._voice_controller
        c.enable()
        c.event(VoiceChannelChanged(ACCOUNT, COMPONENT, True, session_id=c.session_id))
        return window, c

    def test_call_is_unowned_and_chat_remains_usable_until_remote_end(self):
        get_owner = ctypes.WinDLL("user32").GetWindow
        get_owner.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        get_owner.restype = ctypes.c_void_p
        for direction in ("incoming", "outgoing"):
            with self.subTest(direction=direction):
                window, c = self.call_window()
                try:
                    window.ShowWithoutActivating()
                    state = "incoming_ringing" if direction == "incoming" else "ringing"
                    c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid",
                                             direction, state, "", 1, c.session_id, PEER))
                    dialog = window._show_voice_dialog()
                    self.assertIsInstance(dialog, wx.Frame)
                    self.assertIsNone(dialog.GetParent())
                    self.assertFalse(get_owner(dialog.GetHandle(), 4))  # GW_OWNER
                    self.assertTrue(dialog.GetWindowStyleFlag() & wx.MINIMIZE_BOX)
                    self.assertFalse(dialog.GetWindowStyleFlag() & wx.FRAME_NO_TASKBAR)
                    c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid",
                                             direction, "active", "", 2, c.session_id, PEER))
                    self.assertFalse(dialog.GetWindowStyleFlag() & wx.STAY_ON_TOP)
                    window.Iconize(True)
                    wx.YieldIfNeeded()
                    self.assertTrue(dialog.IsShownOnScreen())
                    self.assertFalse(dialog.IsIconized())
                    window.Iconize(False)
                    window.Raise()
                    window.conversation.composer.SetFocus()
                    wx.YieldIfNeeded()
                    self.assertTrue(window.IsEnabled())
                    self.assertIs(wx.Window.FindFocus(), window.conversation.composer)
                    window.conversation.composer.WriteText("Draft during call")
                    window.conversation.messages.SetFocus()
                    window.conversation.messages.SetSelection(0, 7)
                    c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid",
                                             direction, "active", "", 3, c.session_id, PEER))
                    dialog._tick(None)
                    self.assertIs(wx.Window.FindFocus(), window.conversation.messages)
                    self.assertEqual(window.conversation.messages.GetSelection(), (0, 7))
                    window.conversation.composer.SetFocus()
                    c.service.reset_mock()
                    with patch("cliente_xmpp.ui.voice_integration.wx.CallAfter") as later:
                        c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid",
                                                 direction, "ended", "", 4, c.session_id, PEER))
                        later.assert_not_called()  # Do not steal focus from the draft.
                    self.assertIsNone(window._voice_dialog)
                    self.assertTrue(dialog._closing)
                    self.assertIs(wx.Window.FindFocus(), window.conversation.composer)
                    self.assertEqual(window.conversation.composer.GetValue(), "Draft during call")
                    c.service.voice_command.assert_not_called()
                    c.service.stop_voice.assert_not_called()
                    self.assertTrue(c.connected)
                finally:
                    window._closing = True
                    window._close_voice()
                    window.Destroy()
                    wx.YieldIfNeeded()

    def test_independent_call_is_reused_and_closed_by_main_window_shutdown(self):
        window, c = self.call_window()
        try:
            c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "outgoing",
                                     "active", "", 1, c.session_id, PEER))
            dialog = window._show_voice_dialog(activate=False)
            with patch.object(dialog, "present") as present:
                self.assertIs(window._show_voice_dialog(), dialog)
                present.assert_called_once()
            window._closing = True
            sid = c.session_id
            window._close_voice()
            self.assertTrue(dialog._closing)
            self.assertIsNone(window._voice_dialog)
            self.assertFalse(dialog.duration_timer.IsRunning())
            c.service.stop_voice.assert_called_once_with(sid)
            c.service.voice_command.assert_not_called()
        finally:
            window._close_voice()
            window.Destroy()
            wx.YieldIfNeeded()

    def test_contact_header_call_button_is_adjacent_and_only_for_whatsapp_individuals(self):
        frame = wx.Frame(None)
        with patch("cliente_xmpp.ui.conversation_panel.MpvAudioPlayer"), \
                patch("cliente_xmpp.ui.conversation_panel.NvdaSpeaker"):
            panel = ConversationPanel(frame, lambda jid: jid)
        try:
            panel.set_voice_component(COMPONENT)
            panel.set_remote_actions_enabled(True)
            for chat, visible in (
                (Chat(PEER, "Fixture contact"), True),
                (Chat("group@" + COMPONENT, "Fixture group", is_group=True), False),
                (Chat("contact@example.invalid", "XMPP contact"), False),
            ):
                with self.subTest(jid=chat.jid):
                    panel.set_chat(chat)
                    self.assertEqual(panel.call_button.IsShown(), visible)
                    self.assertEqual(panel.call_button.IsEnabled(), visible)
            panel.set_chat(Chat(PEER, "Fixture contact"))
            header = panel.contact_info_button.GetContainingSizer()
            controls = [item.GetWindow() for item in header.GetChildren()]
            self.assertEqual(controls[controls.index(panel.contact_info_button)+1],
                             panel.call_button)
            self.assertIn("Llamar", accessible(panel.call_button)["name"])
            panel.set_remote_actions_enabled(False)
            self.assertFalse(panel.call_button.IsEnabled())
        finally:
            frame.Destroy()

    def test_confirmation_decline_profile_change_and_confirmed_target_are_safe(self):
        class Window(VoiceIntegrationMixin, wx.Frame):
            pass

        for choice in ("no", "yes", "profile"):
            with self.subTest(choice=choice):
                window = Window(None)
                window.current_jid, window.whatsapp_component_jid = ACCOUNT, COMPONENT
                window.whatsapp_verified = True
                window._voice_controller = window._voice_dialog = None
                window.xmpp, window.speaker = Mock(), Mock()
                window._display_name_for_jid = lambda _jid: "Fixture"
                window._show_voice_dialog = Mock()
                window.conversation = SimpleNamespace(current_chat=Chat(
                    PEER, "Roster name", custom_name="Confirmed name",
                ))
                try:
                    def confirm(*_args, window=window, choice=choice, **_kwargs):
                        window.conversation.current_chat = Chat("other@" + COMPONENT, "Other")
                        if choice == "profile":
                            window.current_jid = "other@example.invalid"
                        return wx.NO if choice == "no" else wx.YES

                    with patch("cliente_xmpp.ui.voice_integration.wx.MessageBox",
                               side_effect=confirm) as box:
                        window._on_call_contact()
                    self.assertIn("Estás a punto de llamar a Confirmed name.", box.call_args[0][0])
                    self.assertTrue(box.call_args[0][2] & wx.NO_DEFAULT)
                    window.xmpp.voice_command.assert_not_called()
                    c = window._voice_controller
                    if choice == "yes":
                        self.assertTrue(c.queued_dial)
                        self.assertEqual(c.target, PEER)
                        window._show_voice_dialog.assert_called_once()
                    else:
                        self.assertFalse(c.queued_dial)
                        window._show_voice_dialog.assert_not_called()
                        window.xmpp.start_voice.assert_not_called()
                finally:
                    window.Destroy()

    def test_native_controls_keep_selection_and_escape_rejects_without_disabling_listener(self):
        service = Mock()
        holder = {}
        c = VoiceController(service, ACCOUNT, COMPONENT, lambda: holder["dialog"].render(),
                            Mock(), Mock(), lambda jid: "Fixture contact")
        dialog = VoiceCallDialog(None, c, Mock())
        holder["dialog"] = dialog
        try:
            c.select(PEER, "Fixture contact")
            c.enable()
            c.event(VoiceChannelChanged(ACCOUNT, COMPONENT, True, session_id=c.session_id))
            c.event(VoiceDevicesLoaded(ACCOUNT, c.session_id, (
                VoiceDevice(1, "Fixture mic", True, False),
                VoiceDevice(2, "Fixture output", False, True),
            )))
            self.assertFalse(hasattr(dialog, "call"))
            self.assertFalse(hasattr(dialog, "enable"))
            self.assertIsNone(dialog.GetDefaultItem())
            enter = Mock(GetKeyCode=Mock(return_value=wx.WXK_RETURN))
            dialog._key(enter)
            enter.Skip.assert_called_once()
            service.voice_command.assert_not_called()
            self.assertFalse(dialog.answer.IsEnabled())
            self.assertIsInstance(dialog.mute, wx.CheckBox)
            dialog.input.SetSelection(1)
            c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "incoming",
                                     "incoming_ringing", "", 1, c.session_id, PEER))
            self.assertTrue(dialog.answer.IsEnabled())
            self.assertTrue(dialog.answer.IsShown())
            self.assertFalse(dialog.hangup.IsShown())
            self.assertFalse(dialog.audio_panel.IsShown())
            dialog._toggle_audio_settings(None)
            self.assertTrue(dialog.audio_panel.IsShown())
            self.assertEqual(dialog.input.GetSelection(), 1)
            self.assertFalse(dialog.input.IsFrozen())
            dialog._apply_devices(None)
            self.assertEqual(c.settings.input_device, 1)
            self.assertEqual(dialog.status.GetName(), "Estado de la llamada")
            event = Mock(GetKeyCode=Mock(return_value=wx.WXK_ESCAPE))
            dialog._key(event)
            self.assertFalse(dialog.audio_panel.IsShown())
            service.voice_command.assert_not_called()
            dialog._key(event)
            self.assertEqual(service.voice_command.call_args.args, ("reject",))
            service.stop_voice.assert_not_called()
            self.assertTrue(c.connected)
        finally:
            if not dialog.IsBeingDeleted():
                dialog.Destroy()

    def test_msaa_exposes_names_checkbox_role_and_checked_state_after_theme(self):
        holder = {}
        c = VoiceController(Mock(), ACCOUNT, COMPONENT, lambda: holder["dialog"].render(),
                            Mock(), Mock(), lambda _: "Fixture")
        dialog = VoiceCallDialog(None, c, Mock())
        holder["dialog"] = dialog
        try:
            for name in ("status", "peer", "mute", "audio_settings", "input", "output",
                         "hangup", "answer"):
                with self.subTest(control=name):
                    self.assertTrue(accessible(getattr(dialog, name))["name"])
            before = accessible(dialog.mute)
            self.assertEqual(before["role"], 44)
            self.assertFalse(before["state"] & 0x10)
            dialog.mute.SetValue(True)
            self.assertTrue(accessible(dialog.mute)["state"] & 0x10)
        finally:
            dialog.Destroy()

    def test_duration_keeps_advancing_with_focus_without_text_events_or_announcements(self):
        holder, now = {}, [10.]
        announce = Mock()
        c = VoiceController(Mock(), ACCOUNT, COMPONENT, lambda: holder["dialog"].render(),
                            announce, Mock(), lambda _: "Fixture", clock=lambda: now[0])
        dialog = VoiceCallDialog(None, c, Mock())
        holder["dialog"] = dialog
        try:
            c.enable()
            c.event(VoiceChannelChanged(ACCOUNT, COMPONENT, True, session_id=c.session_id))
            c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "outgoing",
                                     "active", "", 1, c.session_id, PEER))
            announce.reset_mock()
            self.assertTrue(dialog.duration_timer.IsRunning())
            self.assertIn("Duración: 00:00", dialog.status.GetValue())
            with patch.object(dialog.status, "HasFocus", return_value=False):
                now[0] = 75.
                dialog._tick(None)
            self.assertIn("Duración: 01:05", dialog.status.GetValue())
            dialog.status.SetSelection(0, 7)
            text_events = Mock()
            dialog.status.Bind(wx.EVT_TEXT, text_events)
            with patch.object(dialog.status, "HasFocus", return_value=True):
                now[0] = 90.
                dialog._tick(None)
            self.assertIn("Duración: 01:20", dialog.status.GetValue())
            self.assertEqual(dialog.status.GetSelection(), (0, 7))
            event = Mock()
            dialog._status_focus(event)
            self.assertIn("Duración: 01:20", dialog.status.GetValue())
            event.Skip.assert_called_once()
            announce.assert_not_called()
            text_events.assert_not_called()
        finally:
            dialog.Destroy()

    def test_hangup_and_remote_end_close_once_without_disabling_listener(self):
        for ending in ("local", "remote"):
            with self.subTest(ending=ending):
                holder, closed = {}, Mock()
                service = Mock()
                c = VoiceController(service, ACCOUNT, COMPONENT,
                                    lambda holder=holder: holder["dialog"].render(), Mock(), Mock(),
                                    lambda _: "Fixture")
                dialog = VoiceCallDialog(None, c, closed)
                holder["dialog"] = dialog
                try:
                    c.enable()
                    c.event(VoiceChannelChanged(ACCOUNT, COMPONENT, True, session_id=c.session_id))
                    c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "outgoing",
                                             "active", "", 1, c.session_id, PEER))
                    self.assertEqual(dialog.hangup.GetLabel().replace("&", ""), "Colgar")
                    self.assertFalse(hasattr(dialog, "close_button"))
                    c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "outgoing",
                                             "ended", "", 1, c.session_id, PEER))
                    self.assertFalse(dialog._closing)  # stale revision cannot close the call.
                    if ending == "local":
                        dialog.hangup.Command(wx.CommandEvent(wx.wxEVT_BUTTON,
                                                              dialog.hangup.GetId()))
                        service.voice_command.assert_called_once()
                        self.assertEqual(service.voice_command.call_args.args, ("hangup",))
                    else:
                        c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid",
                                                 "outgoing", "ended", "", 2, c.session_id, PEER))
                        service.voice_command.assert_not_called()
                    self.assertTrue(dialog._closing)
                    self.assertFalse(dialog.duration_timer.IsRunning())
                    dialog.dismiss()
                    closed.assert_called_once_with(dialog)
                    service.stop_voice.assert_not_called()
                    self.assertTrue(c.connected)
                finally:
                    if not dialog.IsBeingDeleted():
                        dialog.Destroy()

    def test_incoming_restores_and_focuses_answer_without_permanent_topmost(self):
        holder = {}
        c = VoiceController(Mock(), ACCOUNT, COMPONENT, lambda: holder["dialog"].render(),
                            Mock(), Mock(), lambda _: "Fixture")
        dialog = VoiceCallDialog(None, c, Mock())
        holder["dialog"] = dialog
        try:
            c.enable()
            c.event(VoiceChannelChanged(ACCOUNT, COMPONENT, True, session_id=c.session_id))
            c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "incoming",
                                     "incoming_ringing", "", 1, c.session_id, PEER))
            self.assertTrue(dialog.GetWindowStyleFlag() & wx.STAY_ON_TOP)
            with patch.object(dialog, "IsIconized", return_value=True), \
                    patch.object(dialog, "Iconize") as restore, \
                    patch.object(dialog, "Show") as show, \
                    patch.object(dialog, "Raise") as raise_window, \
                    patch.object(dialog, "RequestUserAttention") as attention, \
                    patch.object(dialog.answer, "SetFocus") as focus:
                dialog.present()
                restore.assert_called_once_with(False)
                show.assert_called_once()
                raise_window.assert_called_once()
                attention.assert_called_once()
                focus.assert_called_once()
            c.event(VoiceStateChanged(ACCOUNT, COMPONENT, "call", "fixture@lid", "incoming",
                                     "active", "", 2, c.session_id, PEER))
            self.assertFalse(dialog.GetWindowStyleFlag() & wx.STAY_ON_TOP)
        finally:
            dialog.Destroy()

    def test_device_refresh_preserves_chosen_identity_when_indices_are_reused(self):
        holder = {}
        c = VoiceController(Mock(), ACCOUNT, COMPONENT, lambda: holder["dialog"].render(),
                            Mock(), Mock(), lambda _: "Fixture")
        dialog = VoiceCallDialog(None, c, Mock())
        holder["dialog"] = dialog
        try:
            c.local_devices((VoiceDevice(1, "Chosen mic", True, False, "driver\0chosen"),))
            dialog.input.SetSelection(1)
            dialog._apply_devices(None)
            self.assertEqual(c.settings.input_identity, "driver\0chosen")
            c.local_devices((VoiceDevice(1, "Other mic", True, False, "driver\0other"),))
            self.assertIn("no disponible", dialog.input.GetStringSelection())
            dialog._apply_devices(None)
            self.assertEqual(c.settings.input_identity, "driver\0chosen")
        finally:
            dialog.Destroy()
