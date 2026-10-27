"""Versioned, account-scoped linking; IQ timeout never means remote cancellation."""
from __future__ import annotations

import asyncio
import base64
import secrets
import time
from xml.etree import ElementTree as ET

from cliente_xmpp.xmpp.events import WhatsAppLinkState

NAMESPACE = "urn:can:whatsapp:linking:1"
DISCO_NS = "http://jabber.org/protocol/disco#info"
STATUSES = {"needs_registration", "needs_pairing", "connecting", "needs_qr", "paired",
            "connected", "needs_pair_code", "connection_error", "logged_out", "canceled",
            "canceling", "expired", "needs_relogin"}


class LinkingProtocol:
    def __init__(self, client):
        self.client = client
        self.supported: dict[str, bool] = {}
        self.states: dict[str, WhatsAppLinkState] = {}
        self.pollers: dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()
        self.wire_lock = asyncio.Lock()
        self.session_generation = client._session_generation
        self.generation = time.monotonic_ns()
        self.pending_requests: dict[str, str] = {}
        self.qr_revisions: dict[str, tuple[str, int]] = {}
        self.closed = False

    async def available(self, component):
        if component in self.supported:
            return self.supported[component]
        # A failed discovery is not evidence of an older bridge. Keep it
        # retryable instead of silently selecting the legacy protocol.
        try:
            info = await self.client["xep_0030"].get_info(jid=component, timeout=8)
        except Exception:
            self.monitor(component)
            raise
        if not self.current():
            raise asyncio.CancelledError
        value = any(e.get("var") == NAMESPACE
                    for e in info.xml.findall(f".//{{{DISCO_NS}}}feature"))
        self.supported[component] = value
        return value

    def current(self):
        return not self.closed and self.session_generation == self.client._session_generation

    async def request(self, component, op="status", **values):
        async with self.wire_lock:
            return await self._request(component, op, **values)

    async def _request(self, component, op="status", **values):
        if not self.current():
            raise asyncio.CancelledError
        started = time.monotonic()
        iq = self.client.make_iq_get() if op == "status" else self.client.make_iq_set()
        iq["to"] = component
        ET.SubElement(iq.xml, f"{{{NAMESPACE}}}query", {"op": op, **values})
        result = await iq.send(timeout=40 if op == "phone" else 15)
        if not self.current():
            raise asyncio.CancelledError
        if result.xml.get("from", "").split("/", 1)[0] != component:
            raise ValueError("Respuesta de otro componente")
        node = result.xml.find(f"{{{NAMESPACE}}}state")
        if node is None or node.get("version") != "1":
            raise ValueError("Respuesta de vinculación incompatible")
        status = node.get("phase", "")
        if status not in STATUSES:
            raise ValueError("Estado de vinculación no reconocido")
        authenticated = node.get("authenticated") == "1"
        if authenticated and status != "connected":
            raise ValueError("Estado de autenticación incoherente")
        previous = self.states.get(component)
        attempt, revision = node.get("attempt", ""), int(node.get("revision", "0"))
        if previous and attempt == previous.attempt and revision < previous.revision:
            return previous
        ttl = min(60.0, max(0.0, float(node.get("qr_ttl", "0"))))
        # Subtract the whole round trip: never extend a QR's server-provided lifetime.
        deadline = started + ttl if ttl else 0.0
        qr_revision = int(node.get("qr_revision", "0"))
        image = b""
        qr = node.find(f"{{{NAMESPACE}}}qr")
        key = attempt, qr_revision
        if (qr is not None and deadline > time.monotonic()
                and self.qr_revisions.get(component) != key):
            if len(qr.text or "") > 256 * 1024:
                raise ValueError("QR demasiado grande")
            image = base64.b64decode(qr.text or "", validate=True)
            if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("QR inválido")
            self.qr_revisions[component] = key
        code = node.findtext(f"{{{NAMESPACE}}}code", "")
        event = WhatsAppLinkState(
            account_jid=str(self.client.boundjid.bare), component_jid=component,
            generation=self.generation, attempt=attempt, revision=revision, status=status,
            authenticated=authenticated, reason=node.get("error") or node.get("reason", ""),
            qr_revision=qr_revision, qr_deadline=deadline, image_data=image, code=code,
            sync_phase=node.get("sync", "pending"),
        )
        self.states[component] = event
        self.client._whatsapp_session_ready = authenticated
        if authenticated and not (previous and previous.authenticated):
            self.client._start_initial_remote_sync_if_ready()
        self.client._emit(event)
        return event

    async def probe(self, component):
        try:
            event = await self.request(component)
            return event.status
        finally:
            # In particular, a failed first IQ must not leave this session
            # without any way to observe recovery.
            self.monitor(component)

    def monitor(self, component):
        if not self.current():
            return
        if component not in self.pollers or self.pollers[component].done():
            self.pollers[component] = asyncio.create_task(self.poll(component))

    async def poll(self, component):
        failures = 0
        while self.current():
            try:
                await asyncio.sleep(min(30, 3 * 2 ** min(failures, 4)))
                if not self.current():
                    return
                if not await self.available(component):
                    # Only a successful feature response permits legacy mode.
                    await self.client._debug_whatsapp_component_commands(component)
                    return
                await self.request(component)
                failures = 0
            except asyncio.CancelledError:
                return
            except Exception:
                if not self.current():
                    return
                failures += 1
                if failures == 3:
                    if not self.supported.get(component):
                        self.client._emit_whatsapp_status(
                            component, "connection_error", "No se pudo consultar WhatsApp."
                        )
                        continue
                    old = self.states.get(component)
                    self.client._whatsapp_session_ready = False
                    self.client._emit(WhatsAppLinkState(
                        str(self.client.boundjid.bare), component, self.generation,
                        old.attempt if old else "", old.revision if old else 0,
                        "connection_error", False, "status_unavailable",
                    ))

    async def start(self, component):
        async with self.lock:
            # Reuse on IQ timeout: a lost response must not create another transaction.
            request = self.pending_requests.setdefault(component, secrets.token_hex(16))
            await self.request(component, "start", request=request)
            await self.probe(component)

    async def cancel(self, component):
        async with self.lock:
            current = self.states.get(component)
            if current is None:
                current = await self.request(component)
            if not current.attempt:
                return False
            # Cancellation must not wait behind a phone-readiness IQ. The attempt
            # and monotone revision reject any delayed response from that operation.
            result = await self._request(component, "cancel", attempt=current.attempt)
            if result.status != "canceled" or result.reason:
                return False
            self.pending_requests.pop(component, None)
            self.qr_revisions.pop(component, None)
            return True

    async def phone(self, component, phone):
        current = await self.request(component)
        if not current.attempt or current.status in {"canceled", "expired", "needs_pairing"}:
            await self.start(component)
            current = self.states[component]
        # The bridge waits for native readiness belonging to this exact attempt.
        await self.request(component, "phone", attempt=current.attempt, phone=phone)

    def close(self):
        self.closed = True
        for task in self.pollers.values():
            task.cancel()
        self.pollers.clear()
