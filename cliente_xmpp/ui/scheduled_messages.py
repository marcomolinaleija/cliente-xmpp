from __future__ import annotations

import wx

from cliente_xmpp.integrations.scheduled_messages import ScheduledMessageService
from cliente_xmpp.models.scheduled_message import format_due
from cliente_xmpp.ui.scheduled_message_dialog import (
    ScheduledMessagesDialog,
    ScheduleMessageDialog,
)


class ScheduledMessagesMixin:
    def _restore_scheduled_focus(self, previous):
        if not self or self._closing:
            return
        if (previous and self.IsDescendant(previous) and previous.IsShownOnScreen()
                and previous.IsEnabled()):
            previous.SetFocus()
        elif self.chat_list.IsShownOnScreen():
            self.chat_list.focus()
        elif self.conversation.IsShownOnScreen():
            self.conversation.messages.SetFocus()

    def _initialize_scheduled_messages(self):
        self._scheduled_service = ScheduledMessageService(
            lambda row: wx.CallAfter(self._send_atajos_message, row)
        )
        self._sync_scheduled_service()
        self._scheduled_service.start()
        self._scheduled_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_scheduled_timer, self._scheduled_timer)
        self._scheduled_timer.Start(1000)
        self._scheduled_last_error = ""

    def _scheduled_contacts(self):
        return [
            (chat.jid, chat.custom_name or self.chat_names_by_jid.get(chat.jid) or chat.name,
             chat.is_group)
            for chat in self.searchable_chats_by_jid.values()
            if chat.is_group or chat.jid in self.roster_jids
        ]

    def _sync_scheduled_service(self):
        service = getattr(self, "_scheduled_service", None)
        if service is not None:
            service.update(
                self.current_jid.split("/", 1)[0],
                bool(self.whatsapp_verified and self.roster_jids and not self._closing
                     and not self._storage_maintenance_in_progress
                     and not self._storage_reset_in_progress),
                self._scheduled_contacts(),
            )

    def _on_scheduled_timer(self, _event):
        if self._closing:
            return
        self._sync_scheduled_service()
        error = self._scheduled_service.error
        if error and error != self._scheduled_last_error:
            self._scheduled_last_error = error
            self.status_bar.SetStatusText(error)
            self.speaker.speak(error)

    def _on_schedule_message(self, _event):
        self._sync_scheduled_service()
        account, _, contacts = self._scheduled_service.snapshot()
        people = [dict(contact, jid=jid) for jid, contact in contacts.items()
                  if not contact["is_group"]]
        if not account or not people:
            wx.MessageBox("Conecta la cuenta y carga sus contactos antes de programar un mensaje.",
                          "Programar mensaje", wx.OK | wx.ICON_INFORMATION, self)
            return
        current = self.conversation.current_chat
        previous_focus = wx.Window.FindFocus()
        dialog = ScheduleMessageDialog(
            self, account, people, self._scheduled_service.create,
            selected_jid=current.jid if current else "",
        )
        try:
            if dialog.ShowModal() == wx.ID_OK:
                row = dialog.saved
                message = f"Mensaje programado para {row['name']}, {format_due(row['due'])}."
                self.status_bar.SetStatusText(message)
                self.speaker.speak(message)
        finally:
            dialog.deactivate()
            dialog.Destroy()
            wx.CallAfter(self._restore_scheduled_focus, previous_focus)

    def _on_scheduled_messages(self, _event):
        self._sync_scheduled_service()
        account = self.current_jid.split("/", 1)[0]
        if not account:
            wx.MessageBox("Selecciona una cuenta antes de consultar sus mensajes programados.",
                          "Mensajes programados", wx.OK | wx.ICON_INFORMATION, self)
            return

        def operation(function):
            def bound(store):
                if self._scheduled_service.snapshot()[0] != account:
                    raise ValueError("La cuenta cambió; cierra este diálogo y vuelve a abrirlo.")
                return function(store)
            return self._scheduled_service.submit(bound)

        previous_focus = wx.Window.FindFocus()
        dialog = ScheduledMessagesDialog(
            self, account,
            lambda state, offset: operation(lambda store: store.list(account, state, offset)),
            lambda identifier: operation(lambda store: store.cancel(identifier, account)),
        )
        try:
            dialog.ShowModal()
        finally:
            dialog.deactivate()
            dialog.Destroy()
            wx.CallAfter(self._restore_scheduled_focus, previous_focus)

    def _close_scheduled_messages(self):
        timer = getattr(self, "_scheduled_timer", None)
        if timer is not None:
            timer.Stop()
        service = getattr(self, "_scheduled_service", None)
        if service is not None:
            service.close()
