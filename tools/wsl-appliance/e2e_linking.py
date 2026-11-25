"""End-to-end linking check over a real Prosody with fictitious accounts.

Real pieces: Prosody (c2s + component), slixmpp on both sides, the bridge's
tools/bridge_linking.py handler, and the client's XmppService/BridgeXmppClient/
LinkingProtocol. Simulated piece: WhatsMeow (QR events, scan, logout, PairPhone).
Nothing here contacts WhatsApp. Secrets come from the environment and are never
printed; QR payloads and codes are fictitious.
"""
from __future__ import annotations

import asyncio
import importlib.abc
import importlib.machinery
import json
import os
import sys
import threading
import time
import types
from types import SimpleNamespace

sys.path.insert(0, "/src")


class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Audio/Windows-only deps are absent from the bridge image; linking never uses them."""

    NAMES = {"sounddevice", "winsound", "winrt", "pywintypes", "win32api", "win32con",
             "win32gui", "comtypes", "soundfile", "pyaudio", "miniaudio"}

    def find_spec(self, name, path, target=None):
        if name.split(".", 1)[0] in self.NAMES:
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
        return None

    def create_module(self, spec):
        module = types.ModuleType(spec.name)
        module.__path__ = []
        module.__getattr__ = lambda attr: type(attr, (), {})
        return module

    def exec_module(self, module):
        pass


sys.meta_path.insert(0, _StubFinder())

from slixmpp import ComponentXMPP  # noqa: E402

from cliente_xmpp.config.settings import ConnectionSettings  # noqa: E402
from cliente_xmpp.xmpp.client import XmppService  # noqa: E402
from cliente_xmpp.xmpp.events import (  # noqa: E402
    WhatsAppLinkCancelResult,
    WhatsAppLinkState,
    XmppConnected,
    XmppError,
)
from tools import bridge_linking  # noqa: E402
from tools.bridge_linking import NAMESPACE, install, state  # noqa: E402

DOMAIN = "xmpp.whatsappcan.local"
COMPONENT = f"whatsapp.{DOMAIN}"
USER = os.environ["E2E_USER"]
PASSWORD = os.environ["E2E_PASSWORD"]
SECRET = os.environ["E2E_COMPONENT_SECRET"]
CA_FILE = os.environ.get("E2E_CA_FILE", "/tls/server.crt")
QR_ROTATION = 4.0
QR_TTL = 20.0

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}{(' - ' + detail) if detail else ''}", flush=True)


# --------------------------------------------------------------------------- bridge side
class NativeSession:
    """Fake WhatsMeow-backed session; install_session wraps these hooks exactly like Slidge's."""

    def __init__(self, gateway):
        self.xmpp = gateway
        self.user_jid = SimpleNamespace(bare=USER)
        self.logged = False
        self.is_logging_in = False
        self.native = None
        self.qr_task = None
        self.logins = 0
        self.devices = []
        loop = gateway.loop

        def disconnect():
            # Runs in a worker thread, like the gopy call.
            asyncio.run_coroutine_threadsafe(self._stop_qr(), loop).result(5)

        def pair_phone(phone):
            if len(phone.lstrip("+")) < 10:
                raise RuntimeError("fixture: phone number rejected")
            return "E2EF-AKE1"

        self.whatsapp = SimpleNamespace(Disconnect=disconnect, PairPhone=pair_phone)
        self.contacts = SimpleNamespace(ready=None)
        self.bookmarks = SimpleNamespace(ready=None)

    async def _stop_qr(self):
        if self.qr_task is not None:
            self.qr_task.cancel()
            self.qr_task = None

    async def _emit_qr(self):
        index = 0
        while True:
            index += 1
            await type(self).on_wa_qr(self, json.dumps({"code": f"fictional-qr-{index}",
                                                        "ttl": QR_TTL}))
            await asyncio.sleep(QR_ROTATION)

    async def login(self):
        self.logins += 1
        loop = asyncio.get_running_loop()
        self.native = loop.create_future()
        self.qr_task = loop.create_task(self._emit_qr())
        return await self.native

    async def on_wa_qr(self, value):
        pass

    async def on_wa_pair(self, device_id):
        self.devices.append(device_id)

    async def on_wa_connect(self, event):
        await self._stop_qr()
        if self.native is not None and not self.native.done():
            if event.Error:
                self.native.set_exception(RuntimeError(event.Error))
            else:
                loop = asyncio.get_running_loop()
                self.contacts.ready = loop.create_future()
                self.contacts.ready.set_result(True)
                self.bookmarks.ready = loop.create_future()
                self.bookmarks.ready.set_result(True)
                self.native.set_result("Logged in")

    async def logout(self):
        pass

    async def on_wa_logged_out(self, event):
        pass


class Gateway(ComponentXMPP):
    def __init__(self):
        super().__init__(COMPONENT, SECRET, "127.0.0.1", 5347)
        self.register_plugin("xep_0030")
        self.GROUPS = False
        self.sessions = {}
        self.status_iqs = []
        self.hints = []
        self.add_filter("in", self._count_in)
        self.add_filter("out", self._count_out)

    def _count_in(self, stanza):
        query = stanza.xml.find(f"{{{NAMESPACE}}}query")
        if query is not None and query.get("op", "status") == "status":
            self.status_iqs.append(time.monotonic())
        return stanza

    def _count_out(self, stanza):
        if stanza.xml.tag.endswith("message") and stanza.xml.find(
                f"{{{NAMESPACE}}}state") is not None:
            self.hints.append(stanza.xml)
        return stanza

    def hint_senders(self):
        return {h.get("from", "") for h in self.hints}

    def raise_if_not_allowed_jid(self, jid):
        if str(jid.bare) != USER:
            from slixmpp.exceptions import XMPPError
            raise XMPPError("forbidden")

    def get_session_from_jid(self, jid):
        return self.sessions.get(str(jid.bare))

    async def login_wrap(self, session):
        return await session.login()


def run_gateway(ready: threading.Event, holder: dict):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    gateway = Gateway()
    session_class = type("Session", (NativeSession,), {})
    sys.modules["tools.session"] = SimpleNamespace(Session=session_class)
    install(gateway)
    session = session_class(gateway)
    gateway.sessions[USER] = session
    holder.update(gateway=gateway, session=session, loop=loop, cls=session_class)
    gateway.add_event_handler("session_start", lambda _: ready.set())
    gateway.connect()
    loop.run_forever()


def on_gateway(holder, coro_factory, timeout=10):
    return asyncio.run_coroutine_threadsafe(coro_factory(), holder["loop"]).result(timeout)


# --------------------------------------------------------------------------- client side
class Recorder:
    def __init__(self):
        self.events = []
        self.cond = threading.Condition()

    def __call__(self, event):
        with self.cond:
            self.events.append((time.monotonic(), event))
            self.cond.notify_all()

    def wait(self, predicate, timeout, since=0.0):
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                for stamp, event in self.events:
                    if stamp >= since and predicate(event):
                        return stamp, event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None, None
                self.cond.wait(remaining)

    def link_states(self, since=0.0):
        return [e for s, e in self.events if s >= since and isinstance(e, WhatsAppLinkState)]


def is_state(**expected):
    def predicate(event):
        return isinstance(event, WhatsAppLinkState) and all(
            getattr(event, key) == value for key, value in expected.items())
    return predicate


def main() -> int:
    ready, holder = threading.Event(), {}
    threading.Thread(target=run_gateway, args=(ready, holder), daemon=True).start()
    check("component connected to Prosody", ready.wait(15))
    owner = lambda: state(holder["session"])  # noqa: E731
    session = holder["session"]

    rec = Recorder()
    service = XmppService(rec)
    service.connect(ConnectionSettings(jid=USER, host="127.0.0.1", port=5222, use_tls=True,
                                       ca_file=CA_FILE), PASSWORD)
    _, connected = rec.wait(lambda e: isinstance(e, (XmppConnected, XmppError)), 20)
    check("client authenticated to Prosody over TLS", isinstance(connected, XmppConnected),
          type(connected).__name__ if connected else "timeout")
    _, first = rec.wait(is_state(status="needs_pairing"), 20)
    check("contract discovered and first snapshot received", first is not None)

    # ---- 1. QR flow -------------------------------------------------------------------
    mark = time.monotonic()
    service.request_whatsapp_relogin(COMPONENT)
    _, qr = rec.wait(lambda e: is_state(status="needs_qr")(e) and bool(e.image_data), 15, mark)
    check("QR PNG delivered in the IQ", qr is not None and qr.image_data.startswith(b"\x89PNG"))
    first_attempt = qr.attempt if qr else ""
    _, rotated = rec.wait(lambda e: is_state(status="needs_qr")(e) and bool(e.image_data)
                          and e.qr_revision > (qr.qr_revision if qr else 0), 12, mark)
    check("QR rotation delivered with a newer revision", rotated is not None)
    scan_at = time.monotonic()
    on_gateway(holder, lambda: _scan(holder))
    stamp, auth = rec.wait(is_state(authenticated=True), 10, scan_at)
    check("scan + connect authenticates the client", auth is not None)
    if stamp:
        # Linking polls every 3 s; the bridge hint must deliver it sooner.
        check("state hint wakes the client before the next poll", stamp - scan_at < 1.5,
              f"{stamp - scan_at:.2f}s")
    _, ready_sync = rec.wait(is_state(authenticated=True, sync_phase="ready"), 10, scan_at)
    check("sync reported ready", ready_sync is not None)
    check("hints carry no QR or code", all(len(list(h.find(f"{{{NAMESPACE}}}state"))) == 0
                                           for h in holder["gateway"].hints)
          and bool(holder["gateway"].hints)
          and holder["gateway"].hint_senders() == {COMPONENT},
          f"{len(holder['gateway'].hints)} hints from {holder['gateway'].hint_senders()}")

    # ---- stable polling ----------------------------------------------------------------
    before = len(holder["gateway"].status_iqs)
    time.sleep(10)
    stable = len(holder["gateway"].status_iqs) - before
    check("stable session is not polled every 3 s", stable <= 1, f"{stable} status IQs in 10 s")

    # ---- 2. logout then relogin (stale token) ---------------------------------------------
    mark = time.monotonic()
    on_gateway(holder, lambda: holder["cls"].logout(session))
    _, relog = rec.wait(is_state(status="needs_relogin"), 10, mark)
    check("logout reaches the client", relog is not None)
    mark = time.monotonic()
    service.request_whatsapp_relogin(COMPONENT)
    _, qr2 = rec.wait(lambda e: is_state(status="needs_qr")(e) and bool(e.image_data), 15, mark)
    check("relogin after logout opens a NEW attempt (point 1)",
          qr2 is not None and qr2.attempt != first_attempt and session.logins == 2,
          f"logins={session.logins}")
    on_gateway(holder, lambda: _scan(holder))
    rec.wait(is_state(authenticated=True), 10, mark)

    # ---- 3. device removed, then phone pairing ------------------------------------------
    mark = time.monotonic()
    on_gateway(holder, lambda: holder["cls"].on_wa_logged_out(session, SimpleNamespace()))
    _, removed = rec.wait(is_state(status="logged_out"), 10, mark)
    check("device removal reported with its reason",
          removed is not None and removed.reason == "device_removed")
    mark = time.monotonic()
    service.request_whatsapp_pair_code(COMPONENT, "+1")
    _, bad = rec.wait(lambda e: isinstance(e, WhatsAppLinkState) and e.reason == "invalid_phone",
                      20, mark)
    check("invalid number refused without killing the attempt (point 3)",
          bad is not None and owner().phase in bridge_linking.LIVE, f"phase={owner().phase}")
    mark = time.monotonic()
    service.request_whatsapp_pair_code(COMPONENT, "+12025550100")
    stamp, code = rec.wait(lambda e: isinstance(e, WhatsAppLinkState) and bool(e.code), 20, mark)
    check("phone code after removal arrives without the 15 s wait (point 2)",
          code is not None and code.code == "E2EF-AKE1" and stamp - mark < 8,
          f"{(stamp - mark):.2f}s" if stamp else "timeout")
    time.sleep(4)
    repeats = [e for e in rec.link_states(stamp or mark) if e.code]
    check("code is announced once despite status re-delivery", len(repeats) == 1,
          f"{len(repeats)} announcements")

    # ---- 4. cancel -----------------------------------------------------------------------
    mark = time.monotonic()
    service.cancel_whatsapp_linking(COMPONENT)
    _, result = rec.wait(lambda e: isinstance(e, WhatsAppLinkCancelResult), 15, mark)
    _, canceled = rec.wait(is_state(status="canceled"), 15, mark)
    check("cancel confirmed end to end", result is not None and result.confirmed
          and canceled is not None)

    # ---- 5. QR expiry ----------------------------------------------------------------------
    mark = time.monotonic()
    service.request_whatsapp_relogin(COMPONENT)
    rec.wait(lambda e: is_state(status="needs_qr")(e) and bool(e.image_data), 15, mark)
    before_logins = session.logins
    on_gateway(holder, lambda: holder["cls"].on_wa_connect(
        session, SimpleNamespace(Error="can-link:expired", JID="")))
    _, expired = rec.wait(is_state(status="expired"), 10, mark)
    check("QR timeout reported as expired (point 5)",
          expired is not None and expired.reason == "expired")
    time.sleep(1)
    check("expired reason survives the failed native login", owner().reason == "expired",
          owner().reason)
    mark = time.monotonic()
    service.request_whatsapp_relogin(COMPONENT)
    _, qr3 = rec.wait(lambda e: is_state(status="needs_qr")(e) and bool(e.image_data), 15, mark)
    check("retry after expiry needs no separate cancel", qr3 is not None
          and session.logins == before_logins + 1)

    # ---- 6. scan racing the cancel ---------------------------------------------------------
    disconnect = session.whatsapp.Disconnect

    def racing_disconnect():
        asyncio.run_coroutine_threadsafe(
            holder["cls"].on_wa_pair(session, "fictional-device"), holder["loop"]).result(5)
        disconnect()

    session.whatsapp.Disconnect = racing_disconnect
    mark = time.monotonic()
    service.cancel_whatsapp_linking(COMPONENT)
    _, race = rec.wait(is_state(status="canceled"), 15, mark)
    session.whatsapp.Disconnect = disconnect
    check("scan that wins the cancel is reported (point 7)",
          race is not None and race.reason == "paired_after_cancel")

    # ---- 7. login deadline is a timer -------------------------------------------------------
    session.qr_task = None
    mark = time.monotonic()

    async def start_without_qr():
        cls = holder["cls"]
        original = cls._emit_qr

        async def silent(self):
            await asyncio.Event().wait()

        cls._emit_qr = silent
        try:
            # Short deadline for the timer only; the native login keeps its 190 s
            # wait, so only the timer can produce connection_timeout here.
            bridge_linking.LOGIN_TIMEOUT = 2.0
            try:
                await state(session).start("e2e-deadline")
            finally:
                bridge_linking.LOGIN_TIMEOUT = 190
            await asyncio.sleep(0.2)
        finally:
            cls._emit_qr = original

    on_gateway(holder, start_without_qr)
    _, timeout_state = rec.wait(is_state(status="connection_error"), 12, mark)
    check("deadline fires from the timer and reaches the client (point 8)",
          timeout_state is not None and timeout_state.reason == "connection_timeout")

    # ---- 8. manual sync retry ---------------------------------------------------------------
    mark = time.monotonic()
    service.request_whatsapp_relogin(COMPONENT)
    rec.wait(lambda e: is_state(status="needs_qr")(e) and bool(e.image_data), 15, mark)
    on_gateway(holder, lambda: _scan(holder))
    rec.wait(is_state(authenticated=True, sync_phase="ready"), 10, mark)

    async def break_sync():
        owner().sync_error = True
        owner().revision += 1
        owner().notify()
        loop = asyncio.get_running_loop()
        session.contacts.ready = loop.create_future()
        session.contacts.ready.set_result(True)

        class Orm:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        holder["gateway"].store = SimpleNamespace(session=Orm)
        session.send_gateway_status = lambda *a, **k: None

    on_gateway(holder, break_sync)
    _, broken = rec.wait(is_state(authenticated=True, sync_phase="error"), 10, mark)
    check("sync error visible to the client", broken is not None)
    mark = time.monotonic()
    service.request_whatsapp_sync(COMPONENT)
    _, fixed = rec.wait(is_state(authenticated=True, sync_phase="ready"), 10, mark)
    check("manual sync retry recovers without relinking", fixed is not None)

    errors = [e for _, e in rec.events if isinstance(e, XmppError)]
    check("no unexpected client errors", not errors, "; ".join(e.message for e in errors)[:300]
          if errors else "")
    leaked = [e for _, e in rec.events
              if type(e).__name__ in {"MessageReceived", "InboxEntryReceived", "ChatMessage"}
              and COMPONENT in repr(e)]
    check("hints never surface as chat messages", not leaked)

    # Positive control: one ordinary chat message from the component IS archived,
    # which proves the MAM query works; the 29+ hints must add nothing to it.
    def control():
        async def run():
            holder["gateway"].make_message(mto=USER, mfrom=COMPONENT, mtype="chat",
                                           mbody="fictional archive control").send()
        return run()

    on_gateway(holder, control)
    time.sleep(1.5)
    archived = mam_count(service)
    check("hints are not archived in MAM (control archived once)", archived == 1,
          f"{archived} archived from component, {len(holder['gateway'].hints)} hints sent")
    service.disconnect()
    failed = [name for name, ok, _ in results if not ok]
    print(json.dumps({"passed": len(results) - len(failed), "failed": failed}), flush=True)
    return 1 if failed else 0


def mam_count(service):
    from xml.etree import ElementTree as ET

    client = service._client

    async def query():
        iq = client.make_iq_set()
        node = ET.SubElement(iq.xml, "{urn:xmpp:mam:2}query", {"queryid": "e2e"})
        form = ET.SubElement(node, "{jabber:x:data}x", {"type": "submit"})
        field = ET.SubElement(form, "{jabber:x:data}field", {"var": "FORM_TYPE",
                                                              "type": "hidden"})
        ET.SubElement(field, "{jabber:x:data}value").text = "urn:xmpp:mam:2"
        field = ET.SubElement(form, "{jabber:x:data}field", {"var": "with"})
        ET.SubElement(field, "{jabber:x:data}value").text = COMPONENT
        found = []

        def collect(msg):
            if msg.xml.find("{urn:xmpp:mam:2}result") is not None:
                found.append(msg)

        client.add_event_handler("mam_result", collect)
        from slixmpp.xmlstream.handler import Callback
        from slixmpp.xmlstream.matcher import MatchXPath
        client.register_handler(Callback("e2e mam", MatchXPath(
            "{jabber:client}message/{urn:xmpp:mam:2}result"), collect))
        await iq.send(timeout=10)
        await asyncio.sleep(1)
        return len(found)

    return asyncio.run_coroutine_threadsafe(query(), service._loop).result(20)


async def _scan(holder):
    session, cls = holder["session"], holder["cls"]
    await cls.on_wa_pair(session, "fictional-device")
    await cls.on_wa_connect(session, SimpleNamespace(Error="", JID=USER))


if __name__ == "__main__":
    code = main()
    os._exit(code)
