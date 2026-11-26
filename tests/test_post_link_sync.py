"""Post-link WhatsApp sync regressions (audit E1-E9, P1-P3, M1-M3). Fictional data only."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from xml.etree import ElementTree as ET

from slixmpp.exceptions import PresenceError
from slixmpp.stanza import Message as StanzaMessage

from cliente_xmpp.config.settings import ConnectionSettings, SettingsStore
from cliente_xmpp.models.chat import Chat
from cliente_xmpp.xmpp.client import BridgeXmppClient
from cliente_xmpp.xmpp.events import (
    ChatActivityLoaded,
    MessageReceived,
    RosterChanged,
    WhatsAppLinkState,
    WhatsAppSyncReady,
    WhatsAppUnreadSummary,
)

ACCOUNT = "owner@example.test"
COMPONENT = "whatsapp.example.test"
CONTACT = f"5550001@{COMPONENT}"
ROOM = f"#120363000000000001@{COMPONENT}"
HISTORY_NS = "urn:can:whatsapp:history:0"


def close_coroutine(coroutine):
    if hasattr(coroutine, "close"):
        coroutine.close()  # The scheduled work itself is covered by its own test.


def stamp(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


class ClientCase(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.client = BridgeXmppClient(
            ConnectionSettings(jid=ACCOUNT), "fixture", self.events.append,
        )
        self.client._session_started_at = datetime.now(UTC) - timedelta(hours=1)

    def stanza(self, xml: str) -> StanzaMessage:
        return StanzaMessage(xml=ET.fromstring(xml), stream=self.client)

    def direct(self, *, delay: timedelta | None = None, marker=False, mid="m-1"):
        extra = f'<delay xmlns="urn:xmpp:delay" stamp="{stamp(delay)}"/>' if delay else ""
        extra += f'<history xmlns="{HISTORY_NS}"/>' if marker else ""
        return self.stanza(f'''<message xmlns="jabber:client" from="{CONTACT}" to="{ACCOUNT}"
            type="chat" id="{mid}"><body>Fixture text</body>{extra}</message>''')

    def received(self):
        return [e for e in self.events if isinstance(e, MessageReceived)]

    def link_state(self, status, authenticated, sync="pending", reason=""):
        return WhatsAppLinkState(ACCOUNT, COMPONENT, 1, "fixture-attempt", 1, status,
                                 authenticated, reason, sync_phase=sync)


class HistoryNeverLooksNewTests(ClientCase):
    """E1: history is persisted, never announced; offline catch-up still notifies."""

    def complete_link(self):
        self.client._note_whatsapp_link_state(COMPONENT, None,
                                              self.link_state("needs_qr", False))
        with patch("asyncio.create_task", side_effect=close_coroutine):
            self.client._note_whatsapp_link_state(
                COMPONENT, None, self.link_state("connected", True, "syncing"))

    def test_direct_history_after_a_new_link_is_silent_but_live_and_offline_notify(self):
        # Before any link in this process: an offline (delayed) message is new.
        self.client._on_message(self.direct(delay=-timedelta(days=3), mid="offline"))
        self.assertTrue(self.received()[-1].notify)
        self.complete_link()
        for case, stanza, notify in (
            ("history older than the link", self.direct(delay=-timedelta(days=3)), False),
            ("live, no delay", self.direct(mid="live"), True),
            ("delayed but newer than the link", self.direct(delay=timedelta(seconds=30),
                                                            mid="queued"), True),
        ):
            with self.subTest(case=case):
                self.events.clear()
                self.client._on_message(stanza)
                self.assertEqual(self.received()[-1].notify, notify)

    def test_explicit_bridge_marker_is_authoritative_everywhere(self):
        self.client._on_message(self.direct(marker=True))
        self.assertFalse(self.received()[-1].notify)
        self.events.clear()
        group = self.stanza(f'''<message xmlns="jabber:client" from="{ROOM}/Someone"
            to="{ACCOUNT}" type="groupchat" id="g-1"><body>Fixture</body>
            <history xmlns="{HISTORY_NS}"/></message>''')
        self.client._on_groupchat_message(group)
        self.assertFalse(self.received()[-1].notify)
        self.events.clear()
        self.client._emit_message_from_stanza(self.direct(marker=True, mid="c-1"),
                                              outgoing=False)
        self.assertFalse(self.received()[-1].notify)
        self.events.clear()
        self.client._emit_message_from_stanza(self.direct(mid="c-2"), outgoing=False)
        self.assertTrue(self.received()[-1].notify)


class HistoryReachesUiSilentlyTests(ClientCase):
    """E1 end to end: history in the open chat sends no read marker to WhatsApp."""

    def test_open_chat_history_has_no_toast_sound_speech_unread_or_marker(self):
        from cliente_xmpp.ui.main_window import MainWindow

        for marker, expect_new in ((True, False), (False, True)):
            with self.subTest(history=marker):
                self.events.clear()
                self.client._on_message(self.direct(marker=marker))
                event = self.received()[-1]
                window = SimpleNamespace(
                    messages_by_chat={}, _is_locally_deleted_message=lambda _: False,
                    _cleared_chat_blocks_timestamp=lambda *_: False,
                    _message_timestamp=lambda _: 0, _set_chat_state=Mock(),
                    _store_message=lambda m: (m, True, []), whatsapp_verified=True,
                    loading_initial_chat_activity=False, _ensure_chat_for_message=Mock(),
                    _observe_atajos_message=Mock(),
                    conversation=SimpleNamespace(
                        IsShown=lambda: True, current_chat=SimpleNamespace(jid=CONTACT),
                        insert_message_sorted=Mock(), refresh_message=Mock()),
                    _update_chat_from_message=Mock(), _auto_download_media_message=Mock(),
                    _mark_current_chat_displayed=Mock(),
                    _show_windows_notification=Mock(return_value=False),
                    _speak_incoming_message=Mock(), _play_incoming_message_sound=Mock(),
                )
                with patch.object(MainWindow, "_request_chat_list_refresh"), \
                        patch("cliente_xmpp.ui.main_window.VoiceIntegrationMixin."
                              "_handle_voice_event", return_value=False):
                    MainWindow._handle_xmpp_event(window, event)
                self.assertEqual(window._mark_current_chat_displayed.called, expect_new)
                self.assertEqual(window._show_windows_notification.called, expect_new)
                self.assertEqual(window._speak_incoming_message.called, expect_new)
                self.assertEqual(window._play_incoming_message_sound.called, expect_new)
                self.assertFalse(
                    window._update_chat_from_message.call_args.kwargs["mark_unread"])
                window.conversation.insert_message_sorted.assert_called_once()  # Still shown.


class UnreadSummaryTests(ClientCase):
    """M1: the phone's unread state arrives as a structured, bodyless summary."""

    def summary(self, sender=COMPONENT, chat=CONTACT, count="4", marked="0"):
        return self.stanza(f'''<message xmlns="jabber:client" from="{sender}" to="{ACCOUNT}"
            type="normal"><unread xmlns="{HISTORY_NS}" chat="{chat}" count="{count}"
            marked="{marked}" group="0"/></message>''')

    def test_only_the_configured_bridge_sets_unread_for_its_own_chats(self):
        self.client._on_message(self.summary())
        self.assertEqual(self.events, [WhatsAppUnreadSummary(CONTACT, 4, False, False)])
        for stanza in (self.summary(sender="intruder@example.test"),
                       self.summary(chat="someone@other.example.test"),
                       self.summary(count="-1"), self.summary(count="many")):
            with self.subTest(xml=ET.tostring(stanza.xml)[:60]):
                self.events.clear()
                self.client._on_message(stanza)
                self.assertEqual(self.events, [])  # Never a chat message either.


class LinkStateTrackingTests(ClientCase):
    """E4/E5: a relink resyncs; sync-ready runs once per session, not per poll."""

    def test_relink_restarts_initial_sync_and_ready_is_signalled_once(self):
        calls = []
        self.client._initial_roster_chats = []
        with patch.object(self.client, "_discover_group_chats_with_retries",
                          lambda chats: calls.append("groups")), \
                patch.object(self.client, "load_inbox", lambda: calls.append("inbox")), \
                patch.object(self.client, "_load_displayed_states",
                             lambda: calls.append("displayed")), \
                patch("asyncio.create_task", side_effect=close_coroutine) as create_task:
            self.client._whatsapp_session_ready = True
            self.client._start_initial_remote_sync_if_ready()
            first = len(calls)
            ready = self.link_state("connected", True, "ready")
            self.client._note_whatsapp_link_state(COMPONENT, None, ready)
            self.client._note_whatsapp_link_state(COMPONENT, ready, ready)
            self.assertEqual(sum(isinstance(e, WhatsAppSyncReady) for e in self.events), 1)
            # A transport drop is not a new link and does not repeat the ready pass.
            self.client._note_whatsapp_link_state(
                COMPONENT, ready, self.link_state("connecting", False, reason="disconnected"))
            self.client._note_whatsapp_link_state(COMPONENT, None, ready)
            self.assertEqual(sum(isinstance(e, WhatsAppSyncReady) for e in self.events), 1)
            self.client._note_whatsapp_link_state(
                COMPONENT, ready, self.link_state("logged_out", False))
            self.client._start_initial_remote_sync_if_ready()
            self.assertEqual(len(calls), first * 2)  # The relink resynchronizes.
            self.client._note_whatsapp_link_state(COMPONENT, None, ready)
            self.assertEqual(sum(isinstance(e, WhatsAppSyncReady) for e in self.events), 2)
            self.assertIsNotNone(self.client._whatsapp_link_completed_at)
            create_task.assert_called()

    def test_sync_ready_refreshes_roster_rejoins_waiting_groups_and_discovers(self):
        self.client._session_generation = 5
        self.client._group_chat_jids.add(ROOM)
        self.client._groups_waiting_bridge_ready = {ROOM, f"#gone@{COMPONENT}"}
        with patch.object(self.client, "get_roster", AsyncMock()), \
                patch.object(self.client, "_build_roster_chats",
                             return_value=[Chat(jid=CONTACT, name="Fixture Contact")]), \
                patch.object(self.client, "_join_group_chat") as join, \
                patch.object(self.client, "_discover_group_chats", AsyncMock()) as discover:
            asyncio.run(self.client._on_whatsapp_sync_ready(COMPONENT, 4))  # Stale session.
            join.assert_not_called()
            discover.assert_not_awaited()
            asyncio.run(self.client._on_whatsapp_sync_ready(COMPONENT, 5))
        join.assert_called_once_with(ROOM)
        discover.assert_awaited_once()
        self.assertEqual(self.client._groups_waiting_bridge_ready, set())
        self.assertIsInstance(self.events[-1], RosterChanged)

    def test_discovery_retries_stop_with_a_replaced_session(self):
        self.client._session_generation = 1
        passes = []

        async def discover(_chats):
            passes.append(1)
            self.client._session_generation = 2

        with patch.object(self.client, "_discover_group_chats", side_effect=discover), \
                patch("cliente_xmpp.xmpp.client.asyncio.sleep", AsyncMock()):
            asyncio.run(self.client._discover_group_chats_with_retries([]))
        self.assertEqual(passes, [1])

    def test_roster_pushes_are_coalesced_into_one_incremental_event(self):
        scheduled = []
        self.client._initial_roster_chats = []
        self.client.loop = SimpleNamespace(call_later=lambda delay, cb: scheduled.append(cb))
        with patch.object(self.client, "_build_roster_chats",
                          return_value=[Chat(jid=CONTACT, name="Fixture Contact")]):
            for _ in range(50):
                self.client._on_roster_update(None)
            self.assertEqual(len(scheduled), 1)
            scheduled[0]()
        self.assertEqual([type(e) for e in self.events], [RosterChanged])


class GroupJoinDuringSyncTests(ClientCase):
    """E6: a join rejected while the bridge syncs groups waits for sync-ready."""

    def test_not_ready_rejection_is_kept_for_sync_ready_and_others_are_not(self):
        from slixmpp.stanza import Presence

        def presence_error(condition, text):
            stanza = Presence(xml=ET.fromstring(f'''<presence xmlns="jabber:client"
                from="{ROOM}/me" type="error"><error type="wait"><{condition}
                xmlns="urn:ietf:params:xml:ns:xmpp-stanzas"/><text
                xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">{text}</text></error></presence>'''))
            future = Mock()
            future.result.side_effect = PresenceError(stanza)
            return future

        with patch.object(self.client, "_schedule_group_rejoin") as rejoin:
            self.client._finish_group_join(ROOM, presence_error(
                "recipient-unavailable", "Legacy session is not fully initialized, retry later"))
            self.assertEqual(self.client._groups_waiting_bridge_ready, {ROOM})
            rejoin.assert_not_called()
            self.client._groups_waiting_bridge_ready = set()
            self.client._finish_group_join(ROOM, presence_error("item-not-found", "gone"))
            self.assertEqual(self.client._groups_waiting_bridge_ready, set())
            rejoin.assert_not_called()  # Not generalized to other rejections.
            self.client._whatsapp_sync_ready_seen = True
            self.client._finish_group_join(ROOM, presence_error(
                "recipient-unavailable", "Legacy session is not fully initialized, retry later"))
            rejoin.assert_called_once_with(ROOM, delay=5)


class InboxFeatureTests(ClientCase):
    """M3: no inbox query (and no 10 s timeout) on servers without XEP-0430."""

    def test_inbox_is_only_queried_when_advertised(self):
        for features, queried in ((["urn:xmpp:mam:2"], False), (["urn:xmpp:inbox:1"], True)):
            with self.subTest(features=features):
                self.client._inbox_supported = None
                info = SimpleNamespace(xml=ET.fromstring(
                    '<query xmlns="http://jabber.org/protocol/disco#info">' + "".join(
                        f'<feature var="{var}"/>' for var in features) + "</query>"))
                disco = SimpleNamespace(get_info=AsyncMock(return_value=info))
                iq = SimpleNamespace(append=Mock(), send=AsyncMock())
                with patch.object(BridgeXmppClient, "__getitem__", return_value=disco), \
                        patch.object(self.client, "make_iq_get", return_value=iq):
                    asyncio.run(self.client.load_inbox())
                self.assertEqual(iq.send.await_count, int(queried))


class LinkingContractParsingTests(unittest.TestCase):
    """E8/P1: optional bridge fields are bounded tokens, never free text."""

    def test_tokens_and_seconds_are_sanitized(self):
        from cliente_xmpp.xmpp.linking import _bounded_seconds, _short_token

        self.assertEqual(_short_token("rate_limited"), "rate_limited")
        self.assertEqual(_short_token("a1b2c3"), "a1b2c3")
        for bad in ("free text", "x" * 65, "<x>", "a;b"):
            self.assertEqual(_short_token(bad), "")
        self.assertEqual(_bounded_seconds("120"), 120.0)
        self.assertEqual(_bounded_seconds("99999"), 3600.0)
        for bad in ("-5", "nan", "x", None):
            self.assertEqual(_bounded_seconds(bad), 0.0)


class UiSyncFeedbackTests(unittest.TestCase):
    """E2/E8/E9: truthful text, one announcement per transition, manual retry heard."""

    def test_sync_detail_is_truthful_about_individual_chats_and_cooldown(self):
        from cliente_xmpp.ui.main_window import whatsapp_sync_detail

        self.assertIn("chats individuales ya funcionan", whatsapp_sync_detail("syncing"))
        self.assertIn("5 minutos", whatsapp_sync_detail("error", "rate_limited", 270))
        self.assertIn("1 minuto;", whatsapp_sync_detail("error", "rate_limited", 20))
        self.assertIn("Reintentar sincronización", whatsapp_sync_detail("error", "failed"))

    def test_transitions_are_announced_once(self):
        from cliente_xmpp.ui.main_window import MainWindow

        window = SimpleNamespace(speaker=Mock(), whatsapp_last_sync_phase="",
                                 whatsapp_sync_retry_pending=False)
        spoken = []
        with patch("cliente_xmpp.ui.main_window.wx.CallAfter",
                   side_effect=lambda fn, text: spoken.append(text)):
            for phase in ("syncing", "syncing", "error", "error", "ready", "ready"):
                MainWindow._announce_whatsapp_sync_transition(window, phase, "detalle")
            window.whatsapp_sync_retry_pending = True
            MainWindow._announce_whatsapp_sync_transition(window, "error", "detalle")
            MainWindow._announce_whatsapp_sync_transition(window, "error", "detalle")
        self.assertEqual(spoken, ["WhatsApp: detalle",
                                  "WhatsApp: contactos y grupos sincronizados.",
                                  "WhatsApp: detalle"])

    def test_menu_waits_for_a_scheduled_bridge_retry(self):
        from cliente_xmpp.ui.main_window import MainWindow

        window = SimpleNamespace(
            current_jid=ACCOUNT, whatsapp_link_generation=1, whatsapp_link_attempt="a",
            whatsapp_link_revision=1, whatsapp_link_protocol=True,
            whatsapp_qr_restart_after_cancel=False, whatsapp_link_state_key=None,
            whatsapp_qr_deadline=0, whatsapp_pair_phone_pending="",
            whatsapp_pairing_bootstrap_generation=0, whatsapp_link_error=None,
            whatsapp_pair_code=None, whatsapp_code_dialog=None, whatsapp_sync_menu_item=Mock(),
            _close_whatsapp_qr_dialog=Mock(), _handle_whatsapp_bridge_status=Mock(),
            whatsapp_verified=True, whatsapp_link_recovering=False, pending_roster_chats=[],
            connection_header=Mock(), status_bar=Mock(),
            _set_whatsapp_remote_actions_enabled=Mock(), _apply_pending_roster_if_ready=Mock(),
        )
        for retry_in, enabled in ((240.0, False), (0.0, True)):
            event = WhatsAppLinkState(ACCOUNT, COMPONENT, 1, "a", 2, "connected", True,
                                      sync_phase="error", sync_reason="rate_limited",
                                      sync_retry_in=retry_in)
            MainWindow._handle_whatsapp_link_state(window, event)
            self.assertEqual(window.whatsapp_sync_menu_item.Enable.call_args.args[0], enabled)
        self.assertIn("limitó", window.status_bar.SetStatusText.call_args_list[0].args[0])


class UiIdentityTests(unittest.TestCase):
    """P1: another linked number is detected, announced once and never deletes data."""

    def test_identity_change_is_detected_persisted_and_suppresses_cached_group_joins(self):
        from cliente_xmpp.ui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as folder:
            store = SettingsStore(Path(folder) / "settings.json")
            window = SimpleNamespace(current_jid=ACCOUNT, settings_store=store,
                                     whatsapp_identity_changed=False, status_bar=Mock(),
                                     speaker=Mock())
            with patch("cliente_xmpp.ui.main_window.wx.CallAfter") as call_after:
                MainWindow._check_whatsapp_identity(window, COMPONENT, "aaa111")
                MainWindow._check_whatsapp_identity(window, COMPONENT, "aaa111")
                self.assertFalse(window.whatsapp_identity_changed)
                call_after.assert_not_called()
                MainWindow._check_whatsapp_identity(window, COMPONENT, "bbb222")
                self.assertTrue(window.whatsapp_identity_changed)
                call_after.assert_called_once()
            self.assertEqual(store.load_whatsapp_identity(ACCOUNT, COMPONENT), "bbb222")
            self.assertEqual(store.load_whatsapp_identity(ACCOUNT, "other.example.test"), "")
            store.save_whatsapp_identity(ACCOUNT, COMPONENT, "not a token")
            self.assertEqual(store.load_whatsapp_identity(ACCOUNT, COMPONENT), "bbb222")

    def test_locked_settings_file_neither_breaks_link_handling_nor_repeats_warning(self):
        from cliente_xmpp.ui.main_window import MainWindow

        store = Mock()
        store.load_whatsapp_identity.return_value = "aaa111"
        store.save_whatsapp_identity.side_effect = PermissionError("settings.json bloqueado")
        window = SimpleNamespace(current_jid=ACCOUNT, settings_store=store,
                                 whatsapp_identity_changed=False, status_bar=Mock(),
                                 speaker=Mock())
        with patch("cliente_xmpp.ui.main_window.wx.CallAfter") as call_after:
            MainWindow._check_whatsapp_identity(window, COMPONENT, "bbb222")
            MainWindow._check_whatsapp_identity(window, COMPONENT, "bbb222")
        self.assertTrue(window.whatsapp_identity_changed)
        call_after.assert_called_once()
        # Repeated polls of the same identity do not touch the settings file again.
        store.load_whatsapp_identity.assert_called_once()
        store.save_whatsapp_identity.assert_called_once()


class UiChatStateTests(unittest.TestCase):
    """M1/E4/E7/P2/P3 on the wx side, with a minimal fake window."""

    def window(self, **extra):
        base = dict(
            conversation=SimpleNamespace(current_chat=None, IsShown=lambda: False),
            IsActive=lambda: True, loading_initial_chat_activity=False,
            pending_chat_activity={}, _update_chat_summary=Mock(),
            whatsapp_verified=True,
        )
        base.update(extra)
        return SimpleNamespace(**base)

    def test_unread_summary_is_authoritative_and_survives_the_initial_window(self):
        from cliente_xmpp.ui.main_window import MainWindow

        refresh = patch.object(MainWindow, "_request_chat_list_refresh")
        refresh.start()
        self.addCleanup(refresh.stop)

        window = self.window()
        MainWindow._handle_whatsapp_unread_summary(window, WhatsAppUnreadSummary(CONTACT, 0, True))
        window._update_chat_summary.assert_called_once_with(CONTACT, unread_count=1,
                                                            is_group=False)
        window = self.window(loading_initial_chat_activity=True)
        window.pending_chat_activity[CONTACT] = ChatActivityLoaded(CONTACT, None, "Fixture")
        MainWindow._handle_whatsapp_unread_summary(window, WhatsAppUnreadSummary(CONTACT, 3))
        self.assertEqual(window.pending_chat_activity[CONTACT].unread_count, 3)
        self.assertEqual(window.pending_chat_activity[CONTACT].preview, "Fixture")
        open_chat = SimpleNamespace(current_chat=SimpleNamespace(jid=CONTACT),
                                    IsShown=lambda: True)
        window = self.window(conversation=open_chat)
        MainWindow._handle_whatsapp_unread_summary(window, WhatsAppUnreadSummary(CONTACT, 3))
        window._update_chat_summary.assert_not_called()  # The user is reading it.

    def test_roster_change_updates_names_without_touching_focus_or_order(self):
        from cliente_xmpp.ui.main_window import MainWindow

        chats = [Chat(jid=CONTACT, name="Fixture Contact")]
        window = self.window(roster_presented=True, _update_chat_names=Mock(),
                             _upsert_discovered_chats=Mock(), chat_list=Mock())
        MainWindow._handle_roster_changed(window, chats)
        window._update_chat_names.assert_called_once_with(chats)
        window._upsert_discovered_chats.assert_called_once_with(chats, update_status=False)
        window.chat_list.focus.assert_not_called()
        window.chat_list.set_chats.assert_not_called()
        self.assertEqual(window.roster_jids, {CONTACT})

    def test_discoveries_before_verification_are_applied_later(self):
        from cliente_xmpp.ui.main_window import MainWindow

        chat = Chat(jid=ROOM, name="Fixture Group", is_group=True)
        window = self.window(whatsapp_verified=False, pending_discovered_chats={},
                             _upsert_discovered_chats=Mock(), _preload_recent_histories=Mock())
        with patch("cliente_xmpp.ui.main_window.VoiceIntegrationMixin._handle_voice_event",
                   return_value=False):
            from cliente_xmpp.xmpp.events import ChatsDiscovered

            MainWindow._handle_xmpp_event(window, ChatsDiscovered([chat]))
        window._upsert_discovered_chats.assert_not_called()
        window.whatsapp_verified = True
        MainWindow._apply_pending_discovered_chats(window)
        window._upsert_discovered_chats.assert_called_once_with([chat])
        self.assertEqual(window.pending_discovered_chats, {})

    def test_no_marker_means_no_history_sort_per_message(self):
        from cliente_xmpp.ui.main_window import MainWindow

        window = SimpleNamespace(synced_displayed_marker_ids_by_chat={},
                                 _displayed_marker_position=Mock(), _chat_by_jid=Mock())
        for _ in range(100):
            MainWindow._apply_synced_chat_displayed(window, CONTACT)
        window._displayed_marker_position.assert_not_called()

    def test_reconnect_keeps_focus_and_open_conversation(self):
        from cliente_xmpp.ui.main_window import MainWindow

        chat_list = Mock()
        chat_list.selected_chat.return_value = Chat(jid=CONTACT, name="Fixture")
        window = SimpleNamespace(
            whatsapp_verified=True, roster_presented=False, _load_cached_chats=lambda: [],
            _debug_perf=Mock(), _set_searchable_chats=Mock(), _merge_chat_lists=lambda a, b: a,
            searchable_chats_by_jid={}, _chats_with_activity=lambda chats: [],
            xmpp=Mock(), chat_list=chat_list, _sort_chats_by_recency=lambda c: c,
            status_bar=Mock(), roster_jids=set(), whatsapp_identity_changed=False,
            _finish_initial_chat_loading_if_needed=Mock(),
        )
        with patch("cliente_xmpp.ui.main_window.wx.CallLater"):
            MainWindow._apply_roster_chats(window, [])
            MainWindow._apply_roster_chats(window, [])  # XMPP reconnect or relink.
        chat_list.focus.assert_called_once_with()
        window.whatsapp_identity_changed = True
        window.xmpp.monitor_group_chats.reset_mock()
        with patch("cliente_xmpp.ui.main_window.wx.CallLater"):
            MainWindow._apply_roster_chats(window, [])
        window.xmpp.monitor_group_chats.assert_not_called()


if __name__ == "__main__":
    unittest.main()
