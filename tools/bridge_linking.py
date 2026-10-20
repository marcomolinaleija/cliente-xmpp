"""Account-owned WhatsApp linking transactions; no credentials in the XMPP contract."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import secrets
import time
from xml.etree import ElementTree as ET

NAMESPACE = "urn:can:whatsapp:linking:1"
LOGIN_TIMEOUT = 190
TERMINAL = {"canceled", "expired", "connection_error", "logged_out", "needs_pairing"}


class LinkingError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class LinkingSession:
    """Lives on the gateway loop, alongside exactly one native client per account."""

    def __init__(self, session, *, clock=time.monotonic):
        self.session = session
        self.clock = clock
        self.attempt = ""
        self.revision = 0
        self.phase = "needs_pairing"
        self.changed_at = self.clock()
        self.reason = ""
        self.authenticated = False
        self.qr = b""
        self.qr_deadline = 0.0
        self.qr_revision = 0
        self.ready = asyncio.Event()
        self.task = None
        self.bootstrap = None
        self.lock = asyncio.Lock()
        self.requests: dict[str, str] = {}
        self.mode = "qr"
        self.sync_error = False
        self.phone_code = ""
        self.phone_fingerprint = ""

    def change(self, phase, reason=""):
        self.phase, self.reason = phase, reason
        self.changed_at = self.clock()
        self.revision += 1
        if phase not in {"needs_qr", "needs_pair_code"}:
            self.qr = b""
            self.qr_deadline = 0.0
            self.ready.clear()
            self.phone_code = self.phone_fingerprint = ""

    def reserve(self):
        self.attempt = secrets.token_hex(16)
        self.authenticated = False
        self.mode = "qr"
        self.sync_error = False
        self.change("connecting")

    def snapshot(self):
        if (not self.authenticated and self.phase in {"connecting", "paired"}
                and self.clock() - self.changed_at > LOGIN_TIMEOUT):
            self.change("connection_error", "connection_timeout")
        remaining = max(0.0, self.qr_deadline - self.clock())
        sync = "pending"
        if self.authenticated:
            contacts = getattr(getattr(self.session, "contacts", None), "ready", None)
            groups = getattr(getattr(self.session, "bookmarks", None), "ready", None)
            if self.sync_error:
                sync = "error"
            elif contacts is not None and contacts.done() and groups is not None and groups.done():
                sync = "ready"
            else:
                sync = "syncing"
        return {
            "version": "1", "attempt": self.attempt, "revision": str(self.revision),
            "phase": self.phase, "reason": self.reason,
            "authenticated": "1" if self.authenticated else "0",
            "qr_revision": str(self.qr_revision), "qr_ttl": f"{remaining:.3f}",
            "sync": sync,
        }

    async def login(self, original):
        if self.task is not None and not self.task.done():
            return await asyncio.shield(self.task)
        if self.phase != "connecting":
            self.reserve()
        attempt = self.attempt

        async def run():
            try:
                return await asyncio.wait_for(original(self.session), LOGIN_TIMEOUT)
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt == self.attempt and not self.authenticated:
                    self.change("connection_error", self.reason or "login_failed")
                    await asyncio.to_thread(self.session.whatsapp.Disconnect)
                raise

        self.task = asyncio.create_task(run())
        return await asyncio.shield(self.task)

    async def start(self, request_id):
        async with self.lock:
            if not request_id or len(request_id) > 128:
                raise LinkingError("invalid_request")
            if request_id in self.requests:
                if self.requests[request_id] != self.attempt:
                    raise LinkingError("stale_attempt")
                return
            if self.authenticated:
                return
            active = any(t is not None and not t.done() for t in (self.task, self.bootstrap))
            active = active or bool(self.attempt and self.phase in {
                "connecting", "needs_qr", "paired", "needs_pair_code",
            })
            if self.attempt and self.phase in TERMINAL - {"canceled", "needs_pairing"}:
                raise LinkingError("cancel_required")
            if not active:
                self.reserve()

                async def bootstrap():
                    try:
                        await self.session.xmpp.login_wrap(self.session)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        if not self.authenticated:
                            self.change("connection_error", "login_failed")
                    finally:
                        self.session.is_logging_in = False

                self.bootstrap = asyncio.create_task(bootstrap())
            self.requests[request_id] = self.attempt
            if len(self.requests) > 32:
                self.requests.pop(next(iter(self.requests)))

    async def cancel(self, attempt):
        async with self.lock:
            if not attempt or attempt != self.attempt:
                raise LinkingError("stale_attempt")
            if self.authenticated:
                raise LinkingError("already_connected")
            if self.phase == "canceled":
                return
            self.change("canceling")
            # Disconnect, never Logout: paired device keys and history survive.
            try:
                await asyncio.wait_for(asyncio.to_thread(self.session.whatsapp.Disconnect), 10)
            except Exception as exc:
                self.change("connection_error", "cancel_failed")
                raise LinkingError("cancel_failed") from exc
            tasks = [t for t in (self.task, self.bootstrap, getattr(self, "phone_task", None))
                     if t and not t.done()]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self.session.logged = False
            self.session.is_logging_in = False
            self.change("canceled")
            self.ready.set()  # Wake pending phone IQs so they reject this canceled attempt now.

    async def phone(self, attempt, phone):
        if not phone or len(phone) > 20 or not phone.lstrip("+").isdigit():
            raise LinkingError("invalid_phone")
        if attempt != self.attempt or not attempt:
            raise LinkingError("stale_attempt")
        try:
            await asyncio.wait_for(self.ready.wait(), 15)
        except TimeoutError as exc:
            raise LinkingError("not_ready") from exc
        fingerprint = hashlib.sha256(phone.encode()).hexdigest()
        async with self.lock:
            if attempt != self.attempt or self.phase in {"canceling", "canceled"}:
                raise LinkingError("stale_attempt")
            if self.authenticated:
                raise LinkingError("already_connected")
            if self.qr_deadline <= self.clock():
                raise LinkingError("expired")
            if self.phone_fingerprint and fingerprint != self.phone_fingerprint:
                raise LinkingError("cancel_required")
            if self.phone_code:
                return self.phone_code
            task = getattr(self, "phone_task", None)
            if task is None or task.done():
                async def request_code():
                    return await asyncio.wait_for(
                        asyncio.to_thread(self.session.whatsapp.PairPhone, phone), 20,
                    )
                self.phone_task = task = asyncio.create_task(request_code())
                self.phone_fingerprint = fingerprint
        try:
            code = await asyncio.shield(task)
        except Exception as exc:
            if attempt == self.attempt and self.phase not in {"canceling", "canceled"}:
                self.change("connection_error", "phone_pair_failed")
            raise LinkingError("phone_pair_failed") from exc
        async with self.lock:
            if attempt != self.attempt or self.phase in {"canceling", "canceled"}:
                raise LinkingError("stale_attempt")
            self.mode = "code"
            self.change("needs_pair_code")
            self.phone_code, self.phone_fingerprint = code, fingerprint
            return code

    async def received_qr(self, value):
        if self.phase in {"canceled", "canceling"} or self.authenticated:
            return None
        try:
            data = json.loads(value)
            raw, ttl = data["code"], float(data["ttl"])
            if not isinstance(raw, str) or not 0 < ttl <= 60:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            self.change("connection_error", "invalid_qr_event")
            return None
        if not self.attempt:
            self.reserve()
        attempt = self.attempt
        self.qr_deadline = self.clock() + ttl
        self.ready.set()  # Native websocket is ready, independently of PNG/HTTP.
        self.phase = "needs_pair_code" if self.mode == "code" else "needs_qr"
        self.revision += 1
        self.qr_revision += 1
        qr_revision = self.qr_revision

        def render():
            import qrcode
            output = io.BytesIO()
            qrcode.make(raw).save(output, format="PNG")
            return output.getvalue()

        try:
            png = await asyncio.to_thread(render)
        except Exception:
            self.change("connection_error", "qr_render_failed")
            return None
        if (attempt == self.attempt and qr_revision == self.qr_revision
                and self.qr_deadline > self.clock()):
            self.qr = png
        return raw


def state(session):
    if not hasattr(session, "_can_linking"):
        session._can_linking = LinkingSession(session)
    return session._can_linking


def install_session(cls):
    """Wrap rather than replace existing contact, media, read-state and voice hooks."""
    if getattr(cls, "_can_linking_installed", False):
        return
    cls._can_linking_installed = True
    original_login, original_qr = cls.login, cls.on_wa_qr
    original_pair, original_connect = cls.on_wa_pair, cls.on_wa_connect
    original_logout, original_unlinked = cls.logout, cls.on_wa_logged_out

    async def login(self):
        return await state(self).login(original_login)

    async def qr(self, value):
        raw = await state(self).received_qr(value)
        if raw is not None:
            await original_qr(self, raw)  # Keep generic XMPP clients supported.

    async def pair(self, device_id):
        owner = state(self)
        if owner.phase not in {"canceling", "canceled"}:
            owner.change("paired")
        await original_pair(self, device_id)  # Persist before considering authentication.

    async def connect(self, event):
        owner = state(self)
        if owner.phase in {"canceling", "canceled"}:
            return
        if event.Error or not event.JID:
            owner.authenticated = False
            reason = event.Error.removeprefix("can-link:") or "invalid_connection"
            owner.change("connecting" if reason == "disconnected" else "connection_error",
                         reason if reason in {"disconnected", "client_outdated", "stream_replaced",
                                             "pair_failed", "expired", "unsupported_pairing"}
                         else "connection_failed")
            if reason == "disconnected":
                return  # WhatsMeow owns normal reconnect; do not fail a pending handshake.
            if not event.Error:
                from types import SimpleNamespace
                event = SimpleNamespace(Error="invalid_connection", JID="")
        else:
            owner.authenticated = True
            owner.change("connected")
        await original_connect(self, event)

    async def logout(self):
        owner = state(self)
        owner.authenticated = False
        owner.change("needs_relogin", "disconnected")
        await original_logout(self)
        current = asyncio.current_task()
        tasks = [task for task in (owner.task, owner.bootstrap,
                                  getattr(owner, "phone_task", None))
                 if task is not None and task is not current and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def unlinked(self, event):
        owner = state(self)
        owner.authenticated = False
        owner.change("logged_out", "device_removed")
        await original_unlinked(self, event)

    cls.login, cls.on_wa_qr = login, qr
    cls.on_wa_pair, cls.on_wa_connect = pair, connect
    cls.logout, cls.on_wa_logged_out = logout, unlinked


def install(gateway):
    from slixmpp.exceptions import XMPPError
    from slixmpp.xmlstream.handler import CoroutineCallback
    from slixmpp.xmlstream.matcher import MatchXPath

    from .session import Session

    install_session(Session)
    original_login_wrap = gateway.login_wrap

    async def monitored_login(session):
        try:
            result = await original_login_wrap(session)
            if not state(session).authenticated:
                session.logged = False
            return result
        except Exception:
            owner = state(session)
            if owner.authenticated:
                owner.sync_error = True
            raise
        finally:
            session.is_logging_in = False

    gateway.login_wrap = monitored_login
    gateway["xep_0030"].add_feature(NAMESPACE)

    async def handle(iq):
        query = iq.xml.find(f"{{{NAMESPACE}}}query")
        if query is None or iq["type"] not in {"get", "set"}:
            return
        sender = iq["from"]
        if not sender.resource or iq["to"].bare != gateway.boundjid.bare:
            raise XMPPError("forbidden")
        gateway.raise_if_not_allowed_jid(sender)
        session = gateway.get_session_from_jid(sender)
        operation = query.get("op", "status")
        if operation != "status" and iq["type"] != "set":
            raise XMPPError("bad-request")
        code = None
        if session is None:
            if operation != "status":
                raise XMPPError("registration-required")
            attrs = {"version": "1", "phase": "needs_registration", "authenticated": "0"}
        else:
            if str(session.user_jid.bare) != str(sender.bare):
                raise XMPPError("forbidden")
            owner = state(session)
            try:
                if operation == "start":
                    await owner.start(query.get("request", ""))
                elif operation == "cancel":
                    await owner.cancel(query.get("attempt", ""))
                elif operation == "phone":
                    code = await owner.phone(query.get("attempt", ""), query.get("phone", ""))
                elif operation != "status":
                    raise LinkingError("unsupported_operation")
            except LinkingError as exc:
                reply = iq.reply()
                reply.xml.append(ET.Element(f"{{{NAMESPACE}}}state", {
                    **owner.snapshot(), "error": exc.reason,
                }))
                reply.send()
                return
            attrs = owner.snapshot()
        reply = iq.reply()
        payload = ET.SubElement(reply.xml, f"{{{NAMESPACE}}}state", attrs)
        if session is not None and owner.qr and float(attrs["qr_ttl"]) > 0:
            ET.SubElement(payload, f"{{{NAMESPACE}}}qr", {"mime": "image/png"}).text = (
                base64.b64encode(owner.qr).decode("ascii")
            )
        if code:
            ET.SubElement(payload, f"{{{NAMESPACE}}}code").text = code
        reply.send()

    gateway.register_handler(CoroutineCallback(
        "CAN WhatsApp linking", MatchXPath(f"{{{gateway.default_ns}}}iq/{{{NAMESPACE}}}query"),
        handle,
    ))
