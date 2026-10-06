from __future__ import annotations

import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timedelta

import wx

from cliente_xmpp.models.scheduled_message import (
    MAX_SCHEDULED_MESSAGE_CHARS,
    POLICIES,
    STATES,
    ScheduleRequest,
    format_due,
    parse_local_schedule,
    validate_due,
    validate_text,
)
from cliente_xmpp.ui.theme import apply_theme

NOTICE = (
    "CAN debe estar abierto, el equipo despierto y la cuenta original conectada para enviar. "
    "La programación se guarda en este equipo. La hora usa la zona horaria de Windows; "
    "se guarda como un instante fijo y no se mueve si después cambias de zona."
)


def _fold(text):
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold())
                   if not unicodedata.combining(c))


class ScheduleReviewDialog(wx.Dialog):
    def __init__(self, parent, name, request):
        super().__init__(parent, title="Confirmar programación", size=(680, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.summary = wx.TextCtrl(
            self, value=(
                f"Contacto: {name}\nCuenta: {request.account}\n"
                f"Fecha y hora: {format_due(request.due)}\n"
                f"Si se retrasa: {POLICIES[request.late_policy]}\n\n{NOTICE}\n\n"
                f"Mensaje completo:\n{request.text}"
            ), style=wx.TE_MULTILINE | wx.TE_READONLY,
        )
        self.summary.SetName("Revisión de destinatario, cuenta, fecha, política y mensaje completo")
        buttons = self.CreateSeparatedButtonSizer(wx.OK | wx.CANCEL)
        self.FindWindowById(wx.ID_OK).SetLabel("Confirmar programación")
        self.FindWindowById(wx.ID_CANCEL).SetLabel("Volver sin guardar")
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.summary, 1, wx.ALL | wx.EXPAND, 12)
        box.Add(buttons, 0, wx.ALL | wx.EXPAND, 12)
        self.SetSizer(box)
        self.SetEscapeId(wx.ID_CANCEL)
        self.Bind(wx.EVT_CHAR_HOOK, self._shortcut)
        apply_theme(self)
        self.CenterOnParent()
        wx.CallAfter(self.summary.SetFocus)

    def _shortcut(self, event):
        if (event.GetKeyCode() == wx.WXK_ESCAPE and not event.AltDown()
                and not event.ControlDown() and not event.ShiftDown()):
            self.EndModal(wx.ID_CANCEL)
        else:
            event.Skip()


class ScheduleMessageDialog(wx.Dialog):
    def __init__(self, parent, account, contacts, save, *, selected_jid=""):
        super().__init__(parent, title="Programar mensaje", size=(740, 640),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._account, self._save = account, save
        self._contacts = sorted(contacts, key=lambda c: (c["name"].casefold(), c["jid"]))
        self._matches = self._contacts.copy()
        self._request_id = str(uuid.uuid4())
        self._active, self._busy = True, False
        self.saved = None
        names = Counter(c["name"] for c in self._contacts)
        self._labels = {
            c["jid"]: c["name"] if names[c["name"]] == 1 else f'{c["name"]} — {c["jid"]}'
            for c in self._contacts
        }
        # Native Windows accessibility associates an edit with its preceding label.
        self.search_label = wx.StaticText(self, label="&Buscar contacto:")
        self.search = wx.SearchCtrl(self)
        self.search.SetName("Buscar contacto; escribe para filtrar")
        self.contacts_label = wx.StaticText(self, label="&Contacto:")
        self.contacts = wx.ListBox(self)
        self.contacts.SetName("Contacto destinatario; flechas para elegir")
        self.message_label = wx.StaticText(
            self, label=f"&Mensaje (multilínea, máximo {MAX_SCHEDULED_MESSAGE_CHARS} caracteres):"
        )
        self.message = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        self.message.SetName(f"Mensaje multilínea; máximo {MAX_SCHEDULED_MESSAGE_CHARS} caracteres")
        self.message.SetMaxLength(MAX_SCHEDULED_MESSAGE_CHARS)
        initial = (datetime.now() + timedelta(minutes=15)).replace(second=0, microsecond=0)
        date_example = initial.strftime("%d/%m/%y")
        self.date_label = wx.StaticText(self, label=f"&Fecha de envío (ejemplo: {date_example}):")
        self.date = wx.TextCtrl(self, value=date_example)
        self.date.SetName(f"Fecha local, día/mes/año; por ejemplo, {date_example}")
        self.hour_label = wx.StaticText(self, label="&Hora de envío (HH:MM, 24 horas):")
        self.hour = wx.TextCtrl(self, value=initial.strftime("%H:%M"))
        self.hour.SetName("Hora local de 24 horas, formato HH:MM")
        self.policy_label = wx.StaticText(self, label="Si se &retrasa:")
        self.policy = wx.Choice(self, choices=list(POLICIES.values()))
        self.policy.SetSelection(0)
        self.policy.SetName("Qué hacer si no se puede enviar a tiempo")
        self.status = wx.StaticText(self, label="")
        self.status.SetName("Estado de la programación")
        self.review = wx.Button(self, label="Revisar y &programar...")
        self.cancel = wx.Button(self, wx.ID_CANCEL, "Cancelar")
        box = wx.BoxSizer(wx.VERTICAL)
        notice = wx.StaticText(self, label=NOTICE)
        notice.Wrap(680)
        box.Add(notice, 0, wx.ALL | wx.EXPAND, 12)
        box.Add(self.search_label, 0, wx.LEFT, 12)
        box.Add(self.search, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        box.Add(self.contacts_label, 0, wx.LEFT, 12)
        box.Add(self.contacts, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        box.Add(self.message_label, 0, wx.LEFT, 12)
        box.Add(self.message, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        fields = wx.FlexGridSizer(cols=2, hgap=12, vgap=8)
        fields.AddGrowableCol(1)
        for label, control in ((self.date_label, self.date),
                               (self.hour_label, self.hour),
                               (self.policy_label, self.policy)):
            fields.Add(label, 0, wx.ALIGN_CENTER_VERTICAL)
            fields.Add(control, 1, wx.EXPAND)
        box.Add(fields, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        box.Add(self.status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer()
        buttons.Add(self.review, 0, wx.RIGHT, 8)
        buttons.Add(self.cancel)
        box.Add(buttons, 0, wx.ALL | wx.EXPAND, 12)
        self.SetSizer(box)
        self.SetMinSize((660, 560))
        self.SetEscapeId(wx.ID_CANCEL)
        self.search.Bind(wx.EVT_TEXT, self._filter)
        self.review.Bind(wx.EVT_BUTTON, self._review)
        self.cancel.Bind(wx.EVT_BUTTON, self._close)
        self.Bind(wx.EVT_CLOSE, self._close)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._destroyed)
        self.Bind(wx.EVT_CHAR_HOOK, self._shortcut)
        self._populate(selected_jid)
        apply_theme(self)
        self.CenterOnParent()
        wx.CallAfter(self.contacts.SetFocus)

    def deactivate(self):
        self._active = False

    def _destroyed(self, event):
        if event.GetEventObject() is self:
            self.deactivate()
        event.Skip()

    def _populate(self, selected_jid=""):
        self.contacts.Set([self._labels[c["jid"]] for c in self._matches])
        if self._matches:
            index = next((i for i, c in enumerate(self._matches) if c["jid"] == selected_jid), 0)
            self.contacts.SetSelection(index)

    def _shortcut(self, event):
        if (event.GetKeyCode() == wx.WXK_ESCAPE and not event.AltDown()
                and not event.ControlDown() and not event.ShiftDown()):
            self._close(event)
            return
        targets = {
            ord("B"): self.search, ord("C"): self.contacts, ord("M"): self.message,
            ord("F"): self.date, ord("H"): self.hour, ord("R"): self.policy,
            ord("P"): self.review,
        }
        if (not self._busy and event.AltDown() and not event.ControlDown()
                and not event.ShiftDown() and event.GetKeyCode() in targets):
            targets[event.GetKeyCode()].SetFocus()
        else:
            event.Skip()

    def _filter(self, _event):
        selected = self.contacts.GetSelection()
        jid = self._matches[selected]["jid"] if selected != wx.NOT_FOUND else ""
        query = _fold(self.search.GetValue()).split()
        self._matches = [c for c in self._contacts if all(
            word in _fold(self._labels[c["jid"]]) for word in query
        )]
        self._populate(jid)

    def _request(self):
        selected = self.contacts.GetSelection()
        if selected == wx.NOT_FOUND:
            raise ValueError("Selecciona un contacto destinatario.")
        due = parse_local_schedule(self.date.GetValue().strip(), self.hour.GetValue().strip())
        policy = tuple(POLICIES)[self.policy.GetSelection()]
        validate_due(due.timestamp(), policy, datetime.now().timestamp())
        return ScheduleRequest(
            self._request_id, self._account, self._matches[selected]["jid"],
            validate_text(self.message.GetValue()), due.timestamp(), policy,
        )

    def _review(self, _event):
        if self._busy:
            return
        try:
            request = self._request()
        except ValueError as exc:
            self.status.SetLabel(str(exc))
            wx.MessageBox(str(exc), "Revisa la programación", wx.OK | wx.ICON_WARNING, self)
            return
        dialog = ScheduleReviewDialog(self, self._labels[request.jid], request)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
        finally:
            dialog.Destroy()
        self._busy = True
        self.status.SetLabel("Guardando. Espera el resultado antes de cerrar...")
        for control in (self.search, self.contacts, self.message, self.date, self.hour,
                        self.policy, self.review, self.cancel):
            control.Disable()
        try:
            future = self._save(request)
            future.add_done_callback(lambda result: wx.CallAfter(self._saved, result))
        except Exception as exc:
            self._save_error(self._error_message(exc))

    def _saved(self, future):
        if not self._active:
            return
        try:
            self.saved = future.result()
        except Exception as exc:
            self._save_error(self._error_message(exc))
            return
        self._busy = False
        self.EndModal(wx.ID_OK)

    def _save_error(self, error):
        self._busy = False
        for control in (self.search, self.contacts, self.message, self.date, self.hour,
                        self.policy, self.review, self.cancel):
            control.Enable()
        self.status.SetLabel(error)
        wx.MessageBox(error, "No se pudo confirmar la programación", wx.OK | wx.ICON_ERROR, self)
        self.review.SetFocus()

    @staticmethod
    def _error_message(exc):
        return str(exc) if isinstance(exc, ValueError) else (
            "No se pudo confirmar el guardado. Conservamos el formulario. "
            "Reintenta sin cambiar los datos o consulta los mensajes programados antes de repetir."
        )

    def _close(self, event):
        if self._busy:
            if isinstance(event, wx.CloseEvent) and event.CanVeto():
                event.Veto()
            return
        self.deactivate()
        self.EndModal(wx.ID_CANCEL)


class ScheduledMessagesDialog(wx.Dialog):
    def __init__(self, parent, account, load, cancel):
        super().__init__(parent, title="Mensajes programados", size=(850, 640),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._account, self._load, self._cancel = account, load, cancel
        self._active, self._busy, self._generation = True, False, 0
        self._reload_pending = False
        self._offset, self._total, self._rows = 0, 0, []
        self._states = ("active", "all", "pending", "held", "uncertain", "failed", "canceled")
        self.filter = wx.Choice(self, choices=[
            "Pendientes y problemas", "Todos", "Pendientes", "Retenidos",
            "Resultado incierto", "Fallidos", "Cancelados",
        ])
        self.filter.SetSelection(0)
        self.filter.SetName("Filtro de estado")
        self.items = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.items.SetName("Mensajes programados; Alt+M enfoca la lista; Enter lee los detalles")
        for column, (label, width) in enumerate((
            ("Contacto", 200), ("Fecha y hora local", 225), ("Estado", 235), ("Origen", 85)
        )):
            self.items.InsertColumn(column, label, width=width)
        self.details = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY)
        self.details.SetName("Detalles y mensaje completo; disponible para lectura y copia")
        self.status = wx.StaticText(self, label="")
        self.refresh = wx.Button(self, label="&Actualizar (F5)")
        self.previous = wx.Button(self, label="A&nterior")
        self.next = wx.Button(self, label="&Siguiente")
        self.cancel_message = wx.Button(self, label="&Cancelar mensaje...")
        self.close = wx.Button(self, wx.ID_CANCEL, "Cerrar")
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(wx.StaticText(self, label=f"Cuenta: {account}. {NOTICE}"),
                0, wx.ALL | wx.EXPAND, 12)
        box.GetChildren()[0].GetWindow().Wrap(800)
        box.Add(wx.StaticText(self, label="&Estado:"), 0, wx.LEFT, 12)
        box.Add(self.filter, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        box.Add(self.items, 2, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        box.Add(wx.StaticText(self, label="&Detalles:"), 0, wx.LEFT, 12)
        box.Add(self.details, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        box.Add(self.status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        for button in (self.refresh, self.previous, self.next, self.cancel_message):
            buttons.Add(button, 0, wx.RIGHT, 8)
        buttons.AddStretchSpacer()
        buttons.Add(self.close)
        box.Add(buttons, 0, wx.ALL | wx.EXPAND, 12)
        self.SetSizer(box)
        self.SetMinSize((780, 560))
        self.SetEscapeId(wx.ID_CANCEL)
        self.filter.Bind(wx.EVT_CHOICE, self._filtered)
        self.items.Bind(wx.EVT_LIST_ITEM_SELECTED, self._selected)
        self.items.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _event: self.details.SetFocus())
        self.refresh.Bind(wx.EVT_BUTTON, lambda _event: self._refresh())
        self.previous.Bind(wx.EVT_BUTTON, lambda _event: self._page(-50))
        self.next.Bind(wx.EVT_BUTTON, lambda _event: self._page(50))
        self.cancel_message.Bind(wx.EVT_BUTTON, self._cancel_selected)
        self.close.Bind(wx.EVT_BUTTON, self._close)
        self.Bind(wx.EVT_CLOSE, self._close)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._destroyed)
        self.Bind(wx.EVT_CHAR_HOOK, self._shortcut)
        apply_theme(self)
        self.CenterOnParent()
        wx.CallAfter(self._refresh)
        wx.CallAfter(self.items.SetFocus)

    def deactivate(self):
        self._active = False
        self._generation += 1

    def _destroyed(self, event):
        if event.GetEventObject() is self:
            self.deactivate()
        event.Skip()

    def _shortcut(self, event):
        if (event.GetKeyCode() == wx.WXK_ESCAPE and not event.AltDown()
                and not event.ControlDown() and not event.ShiftDown()):
            self._close(event)
            return
        targets = {ord("M"): self.items, ord("E"): self.filter, ord("D"): self.details}
        if (event.AltDown() and not event.ControlDown() and not event.ShiftDown()
                and event.GetKeyCode() in targets):
            targets[event.GetKeyCode()].SetFocus()
        elif event.GetKeyCode() == wx.WXK_F5 and not self._busy:
            self._refresh()
        else:
            event.Skip()

    def _filtered(self, _event):
        self._offset = 0
        if self._busy:
            self._reload_pending = True
            return
        self._refresh()

    def _page(self, step):
        if not self._busy:
            self._offset = max(0, self._offset + step)
            self._refresh()

    def _enable(self, enabled):
        # Keep focused controls alive during a refresh; disabling a Choice moves
        # keyboard focus. Filter changes are coalesced rather than lost.
        self.refresh.Enable(True)
        self.filter.Enable(True)
        self.previous.Enable(enabled and self._offset > 0)
        self.next.Enable(enabled and self._offset + 50 < self._total)
        row = self._selection()
        self.cancel_message.Enable(enabled and bool(row) and row["state"] in {"pending", "held"})

    def _refresh(self):
        if not self._active or self._busy:
            return
        self._busy = True
        self._generation += 1
        generation = self._generation
        self._enable(False)
        self.status.SetLabel("Consultando la cola local...")
        try:
            future = self._load(self._states[self.filter.GetSelection()], self._offset)
            future.add_done_callback(lambda result: wx.CallAfter(self._loaded, generation, result))
        except Exception:
            self._busy = False
            self.status.SetLabel(
                "No se pudo consultar la cola. Cierra el diálogo y vuelve a abrirlo."
            )
            self._enable(True)

    def _loaded(self, generation, future):
        if not self._active or generation != self._generation:
            return
        self._busy = False
        if self._reload_pending:
            self._reload_pending = False
            self._refresh()
            return
        try:
            rows, total = future.result()
        except Exception as exc:
            self.status.SetLabel(str(exc) if isinstance(exc, ValueError) else
                                 "No se pudo consultar la cola local; vuelve a actualizar.")
            self._enable(True)
            return
        if not rows and total and self._offset >= total:
            self._offset = ((total - 1) // 50) * 50
            self._refresh()
            return
        if not total:
            self._offset = 0
        # Preserve identity, focus and scroll on explicit refreshes. Never poll/rebuild
        # while someone is reading the full message with their screen reader.
        selected = self._selection()
        identity = selected["id"] if selected else ""
        top = self.items.GetTopItem()
        self._rows, self._total = rows, total
        self.items.Freeze()
        try:
            self.items.DeleteAllItems()
            for row in rows:
                index = self.items.InsertItem(self.items.GetItemCount(), str(row["name"])[:160])
                self.items.SetItem(index, 1, format_due(row["due"]))
                self.items.SetItem(index, 2, STATES.get(row["state"], row["state"]))
                self.items.SetItem(index, 3, "CAN" if row["origin"] == "native" else "Atajos")
            if rows:
                index = next((i for i, row in enumerate(rows) if row["id"] == identity), 0)
                self.items.Select(index)
                self.items.Focus(index)
                if 0 <= top < len(rows):
                    self.items.EnsureVisible(top)
        finally:
            self.items.Thaw()
        self._selected(None)
        self.status.SetLabel(
            f"{total} mensajes. Mostrando {self._offset + 1 if rows else 0} "
            f"a {self._offset + len(rows)}. Atajos pausado no envía sus pendientes."
        )
        self._enable(True)

    def _selection(self):
        index = self.items.GetFirstSelected()
        return self._rows[index] if 0 <= index < len(self._rows) else None

    def _selected(self, _event):
        row = self._selection()
        value = ""
        if row:
            value = (
                f"Contacto: {row['name']}\nCuenta: {self._account}\n"
                f"Fecha y hora: {format_due(row['due'])}\n"
                f"Estado: {STATES.get(row['state'], row['state'])}\n"
                f"Si se retrasa: {POLICIES[row['late_policy']]}\n"
                f"Detalle: {row['detail'] or 'Sin incidencias'}\n\n{row['body']}"
            )
        if self.details.GetValue() != value:
            self.details.ChangeValue(value)
        self._enable(not self._busy)

    def _cancel_selected(self, _event):
        row = self._selection()
        if self._busy or not row or row["state"] not in {"pending", "held"}:
            return
        answer = wx.MessageBox(
            f"¿Cancelar el mensaje para {row['name']} del {format_due(row['due'])}?\n"
            "No se puede cancelar si el envío ya comenzó.",
            "Cancelar mensaje programado", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self,
        )
        if answer != wx.YES:
            return
        self._busy = True
        self._enable(False)
        self.status.SetLabel("Cancelando...")
        try:
            future = self._cancel(row["id"])
            future.add_done_callback(lambda result: wx.CallAfter(self._canceled, result))
        except Exception:
            self._busy = False
            self.status.SetLabel("No se pudo confirmar la cancelación; actualiza antes de repetir.")
            self._enable(True)

    def _canceled(self, future):
        if not self._active:
            return
        self._busy = False
        try:
            canceled = future.result()
        except Exception as exc:
            self.status.SetLabel(str(exc))
            self._enable(True)
            return
        if not canceled:
            wx.MessageBox("El envío ya comenzó o su estado cambió; no se pudo cancelar.",
                          "Revisa el mensaje", wx.OK | wx.ICON_WARNING, self)
        self._refresh()

    def _close(self, _event):
        self.deactivate()
        self.EndModal(wx.ID_CANCEL)
