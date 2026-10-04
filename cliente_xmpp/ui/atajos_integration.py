from __future__ import annotations

from datetime import datetime

import wx

from cliente_xmpp.integrations.atajos_api import LocalAssistantAPI
from cliente_xmpp.integrations.atajos_credentials import integration_token
from cliente_xmpp.models.chat import Message
from cliente_xmpp.storage.assistant_media import AssistantMediaStore
from cliente_xmpp.storage.conversation_context import ConversationContextStore


class AtajosIntegrationMixin:
    """All UI access stays on wx; API and outbox work run in their own worker."""

    def _initialize_atajos_integration(self) -> None:
        self._atajos_api = None
        self._atajos_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_atajos_timer, self._atajos_timer)
        self.settings_panel.atajos_api_enabled.SetValue(
            self.settings_store.load_atajos_api_enabled()
        )
        self.settings_panel.atajos_api_enabled.Bind(wx.EVT_CHECKBOX, self._on_atajos_toggle)
        if self.settings_panel.atajos_api_enabled.GetValue():
            self._start_atajos_api()

    def _on_atajos_toggle(self, _event: wx.CommandEvent) -> None:
        enabled = self.settings_panel.atajos_api_enabled.GetValue()
        try:
            self.settings_store.save_atajos_api_enabled(enabled)
        except OSError:
            self.settings_panel.atajos_api_status.SetLabel("No se pudo guardar la preferencia.")
            self.settings_panel.atajos_api_enabled.SetValue(not enabled)
            return
        if enabled:
            self._start_atajos_api()
        else:
            self._close_atajos_api()
            self.settings_panel.atajos_api_status.SetLabel(
                "Integración desactivada. Los mensajes pendientes se conservan sin enviarse."
            )

    def _start_atajos_api(self) -> None:
        if self._atajos_api is not None:
            return
        try:
            token = integration_token()
            self._atajos_api = LocalAssistantAPI(
                token,
                lambda row: wx.CallAfter(self._send_atajos_message, row),
                read_context=ConversationContextStore(self.message_store.path).read_page,
                media_store=AssistantMediaStore(self.message_store.path),
            )
            self._sync_atajos_api()
            self._atajos_api.start()
            self._atajos_timer.Start(3000)
            self.settings_panel.atajos_api_status.SetLabel(
                "Integración local activada. Atajos detecta la conexión automáticamente."
            )
        except Exception:
            self._atajos_api = None
            self.settings_panel.atajos_api_status.SetLabel(
                "No se pudo activar la integración o guardar su credencial de Windows."
            )

    def _on_atajos_timer(self, _event: wx.TimerEvent) -> None:
        if self._atajos_api is not None:
            self._sync_atajos_api()
            if self._atajos_api.error:
                self.settings_panel.atajos_api_status.SetLabel(self._atajos_api.error)

    def _sync_atajos_api(self) -> None:
        account = self.current_jid.split("/", 1)[0]
        contacts = [
            (
                chat.jid,
                chat.custom_name or self.chat_names_by_jid.get(chat.jid) or chat.name,
                chat.is_group,
            )
            for chat in self.searchable_chats_by_jid.values()
            if chat.is_group or chat.jid in self.roster_jids
        ]
        ready = bool(self.whatsapp_verified and self.roster_jids)
        self._atajos_api.update(account, ready, contacts)
        groups = sorted(self._atajos_api.monitored_group_jids(account))
        if groups:
            offset = getattr(self, "_atajos_group_rotation", 0) % len(groups)
            batch = (groups + groups)[offset : offset + min(10, len(groups))]
            self._atajos_group_rotation = offset + len(batch)
            api = self._atajos_api
            self.xmpp.monitor_group_chats(
                batch,
                expected_account=account,
                authorization=lambda jid: jid in api.monitored_group_jids(account),
            )

    def _send_atajos_message(self, row: dict[str, object]) -> None:
        api = self._atajos_api
        if api is None:
            return
        account = self.current_jid.split("/", 1)[0]
        jid = str(row["jid"])
        chat = self.searchable_chats_by_jid.get(jid)
        if self._closing or account != row["account"] or not self.whatsapp_verified:
            api.rejected(
                str(row["id"]),
                "Esperando la cuenta y conexión originales.",
                wait=row["late_policy"] == "send-when-connected",
            )
            return
        if (
            chat is None
            or chat.is_group != bool(row.get("is_group", False))
            or not chat.is_group
            and jid not in self.roster_jids
        ):
            api.rejected(str(row["id"]), "El contacto cambió antes del envío; revisa el mensaje.")
            return
        if row.get("rule_id") and not api.can_dispatch_on_ui(row):
            api.rejected(str(row["id"]), "La regla se detuvo o el chat cambió antes del envío.")
            return
        message_id = "cliente-xmpp-api-" + str(row["id"])
        message = Message(
            chat_jid=jid,
            sender_jid="me",
            sender_name="Tú",
            body=str(row["body"]),
            sent_at=datetime.now().astimezone(),
            outgoing=True,
            message_id=message_id,
            delivery_state="pending",
            chat_is_group=chat.is_group,
        )
        try:
            self._add_pending_outgoing_message(message)
            # Delivery events are shared with normal composer messages.
            self.xmpp.send_message(
                jid,
                message.body,
                message_id=message_id,
                is_group=chat.is_group,
                expected_account=account,
                on_deferred=lambda: wx.CallAfter(self._defer_atajos_message, api, row),
                authorization=(lambda: api.can_dispatch_on_ui(row)) if row.get("rule_id") else None,
            )
        except Exception:
            api.unconfirmed(str(row["id"]))

    def _defer_atajos_message(self, api: LocalAssistantAPI, row: dict[str, object]) -> None:
        if not self._closing:
            self._remove_failed_local_message(str(row["jid"]), "cliente-xmpp-api-" + str(row["id"]))
        api.rejected(
            str(row["id"]),
            "La respuesta automática perdió su permiso, conversación o conexión; no se reintentó."
            if row.get("rule_id")
            else "Esperando la cuenta y conexión originales.",
            wait=row["late_policy"] == "send-when-connected",
        )

    def _observe_atajos_message(self, message: Message, *, live: bool, added: bool) -> None:
        api = getattr(self, "_atajos_api", None)
        if api is None:
            return
        if message.retracted or message.edited:
            kind = "changed"
        elif message.outgoing and live:
            kind = "outgoing"
        elif live and added:
            kind = (
                "incoming"
                if message.body
                and len(message.body) <= 4000
                and not message.media_kind
                and not message.poll
                and not message.call
                else "changed"
            )
        else:
            return
        api.observe_message(self.current_jid.split("/", 1)[0], message, kind)

    def _close_atajos_api(self) -> None:
        api = getattr(self, "_atajos_api", None)
        if api is not None:
            api.close()
            self._atajos_api = None
        timer = getattr(self, "_atajos_timer", None)
        if timer is not None:
            timer.Stop()
