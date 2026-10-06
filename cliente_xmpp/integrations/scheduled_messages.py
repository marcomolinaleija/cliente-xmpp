from __future__ import annotations

import asyncio
import threading
import time
import uuid
from concurrent.futures import Future
from pathlib import Path

from cliente_xmpp.config.settings import APP_DIR
from cliente_xmpp.models.scheduled_message import ScheduleRequest, validate_due, validate_text
from cliente_xmpp.storage.scheduled_messages import ScheduledMessageStore


async def dispatch_due(store, snapshot, send, clock, stopped, origins, authorize=None):
    """Shared dispatch rules; claims and cancellation race atomically in SQLite."""
    account, _, _ = snapshot()
    rows = await asyncio.to_thread(store.due, clock(), account, origins)
    for row in rows:
        if stopped():
            return
        if row["rule_id"] and (authorize is None or not await authorize(row)):
            await asyncio.to_thread(
                store.transition, row["id"], "pending", "canceled",
                "La regla caducó o el chat cambió; no se envió.",
            )
            continue
        account, ready, contacts = snapshot()
        if row["account"] != account:
            continue  # A different account never consumes or changes this backlog.
        if row["late_policy"] == "send-when-connected" and not ready:
            continue
        contact = contacts.get(row["jid"])
        reason = ""
        if contact is None or bool(contact["is_group"]) != bool(row["is_group"]):
            reason = "El contacto ya no está disponible o cambió su identidad."
        elif not ready:
            reason = "La conexión no estaba disponible a la hora indicada."
        elif row["late_policy"] == "hold" and clock() - float(row["due"]) > 30:
            reason = "La hora pasó mientras el cliente estaba cerrado o no disponible."
        if reason:
            await asyncio.to_thread(
                store.transition, row["id"], "pending", "held", reason + " No se envió."
            )
            continue
        if clock() < float(row["due"]) or stopped():
            continue
        claimed = await asyncio.to_thread(store.transition, row["id"], "pending", "dispatching")
        if claimed:
            try:
                send(row)
            except Exception:
                await asyncio.to_thread(
                    store.transition, row["id"], "dispatching", "uncertain",
                    "No se pudo confirmar el envío. Comprueba el chat antes de repetir.",
                )


class ScheduledMessageService:
    """One outbox owner/coordinator, independent of HTTP, credentials and wx."""

    def __init__(self, send, *, path: Path = APP_DIR / "assistant-outbox.sqlite3", clock=time.time):
        self._send, self._path, self._clock = send, path, clock
        self._lock = threading.Lock()
        self._account, self._ready, self._contacts = "", False, {}
        self._assistant = None
        self._closed = threading.Event()
        self._loop = None
        self._stopped = None
        self.ready: Future = Future()
        self.error = ""
        self._thread = threading.Thread(target=self._run, name="can-scheduled-outbox", daemon=True)

    def start(self):
        self._thread.start()

    def update(self, account, ready, contacts):
        with self._lock:
            self._account, self._ready = account, ready
            self._contacts = {
                jid: {"name": name, "is_group": group} for jid, name, group in contacts
            }

    def snapshot(self):
        with self._lock:
            return self._account, self._ready, self._contacts.copy()

    def set_assistant(self, api):
        with self._lock:
            self._assistant = api

    def _run(self):
        try:
            asyncio.run(self._serve())
        except Exception:
            self.error = "La cola de mensajes se detuvo; reinicia CAN y revisa los programados."
        finally:
            self._closed.set()
            if not self.ready.done():
                self.ready.set_exception(RuntimeError("No se pudo abrir la cola de mensajes."))

    async def _serve(self):
        self._loop = asyncio.get_running_loop()
        self._stopped = asyncio.Event()
        store = await asyncio.to_thread(ScheduledMessageStore, self._path)
        self.ready.set_result(store)
        while not self._closed.is_set():
            await self.tick(store)
            try:
                await asyncio.wait_for(self._stopped.wait(), 1)
            except TimeoutError:
                pass

    async def tick(self, store):
        # Native and assistant queues get separate bounded pages, so paused or
        # disconnected assistant work cannot starve native messages.
        await dispatch_due(
            store, self.snapshot, self._send, self._clock, self._closed.is_set, ("native",)
        )
        with self._lock:
            api = self._assistant
        if (api is not None and api._loop is not None and not api._closed.is_set()
                and api._dispatch_ready.is_set()):
            coroutine = api.tick()
            try:
                try:
                    future = asyncio.run_coroutine_threadsafe(coroutine, api._loop)
                except RuntimeError:
                    coroutine.close()
                    raise
                await asyncio.wait_for(asyncio.wrap_future(future), 5)
            except asyncio.CancelledError:
                # Closing/replacing the API cancels its tasks, not the native service.
                if not api._closed.is_set():
                    self.error = "Atajos interrumpió la consulta; sus pendientes esperan."
            except Exception:
                # HTTP/automation trouble must not stop native dispatch.
                self.error = "Atajos no respondió a la cola; sus pendientes esperan."

    def submit(self, operation, *args) -> Future:
        result = Future()

        async def execute(store):
            if self._closed.is_set():
                raise RuntimeError("La cola está cerrada. Reinicia CAN y revisa los programados.")
            return await asyncio.to_thread(operation, store, *args)

        def prepared(ready):
            if not result.set_running_or_notify_cancel():
                return
            try:
                store = ready.result()
                if self._closed.is_set():
                    raise RuntimeError("La cola está cerrada; reinicia CAN.")
                coroutine = execute(store)
                try:
                    pending = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
                except RuntimeError:
                    coroutine.close()
                    raise
            except Exception as exc:
                result.set_exception(exc)
                return

            def finished(future):
                try:
                    result.set_result(future.result())
                except Exception as exc:
                    result.set_exception(exc)
            pending.add_done_callback(finished)
        self.ready.add_done_callback(prepared)
        return result

    def create(self, request: ScheduleRequest) -> Future:
        def save(store):
            account, _, contacts = self.snapshot()
            if not account or account != request.account:
                raise ValueError("La cuenta cambió; vuelve a abrir el diálogo.")
            contact = contacts.get(request.jid)
            if contact is None or contact["is_group"]:
                raise ValueError("El contacto ya no está disponible; vuelve a seleccionarlo.")
            if str(uuid.UUID(request.request_id)) != request.request_id:
                raise ValueError("Identificador de solicitud no válido.")
            text = validate_text(request.text)
            # A persisted retry returns its original row even if its date has passed.
            old = store.request(request.request_id, request.account)
            if not old:
                validate_due(request.due, request.late_policy, self._clock())
            return store.create(
                request.request_id, account,
                [{"jid": request.jid, "name": contact["name"], "text": text}],
                request.due, request.late_policy, origin="native",
            )[0]
        return self.submit(save)

    def can_dispatch_on_ui(self, row):
        account, ready, contacts = self.snapshot()
        contact = contacts.get(row["jid"])
        now = self._clock()
        return bool(
            not self._closed.is_set() and ready and account == row["account"]
            and contact and bool(contact["is_group"]) == bool(row["is_group"])
            and now >= float(row["due"])
            and (row["late_policy"] != "hold" or now - float(row["due"]) <= 30)
        )

    def delivery(self, message_id, state):
        if message_id.startswith("cliente-xmpp-api-"):
            self._record(lambda store: store.finish_delivery(message_id, state))

    def unconfirmed(self, item_id):
        self._record(lambda store: store.transition(
            item_id, "dispatching", "uncertain", "No se confirmó el envío; revisa el chat."
        ))

    def rejected(self, item_id, reason, *, wait=False):
        self._record(lambda store: store.transition(
            item_id, "dispatching", "pending" if wait else "held", reason
        ))

    def _record(self, operation):
        def finished(future):
            try:
                future.result()
            except Exception:
                self.error = "No se pudo guardar un estado de envío; revisa antes de repetir."
        self.submit(operation).add_done_callback(finished)

    def close(self):
        self._closed.set()
        if self._loop and self._stopped and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._stopped.set)
            except RuntimeError:
                pass  # The worker can finish between the state check and the call.

    def wait_closed(self, timeout: float = 10) -> bool:
        """Only for a background cleanup worker; never wait on the wx thread."""
        if self._thread.ident is not None:
            self._thread.join(timeout)
        return not self._thread.is_alive()
