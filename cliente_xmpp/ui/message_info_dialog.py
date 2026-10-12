from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import wx

from cliente_xmpp.models.message_info import (
    GroupMessageInfo,
    ParticipantReceipt,
    merge_message_info,
)
from cliente_xmpp.ui.theme import apply_theme

InfoCallback = Callable[[GroupMessageInfo | None, str, bool], None]
InfoLoader = Callable[[InfoCallback], None]
REFRESH_INTERVAL_MS = 2000
SECTION_TITLES = ("Leído", "Entregado", "Sin entrega confirmada")


def receipt_section(row: ParticipantReceipt) -> int:
    return 0 if row.read_at else 1 if row.delivered_at else 2


def same_view_row(left: ParticipantReceipt | str, right: ParticipantReceipt | str) -> bool:
    if isinstance(left, str) or isinstance(right, str):
        return left == right
    return bool((left.lid and left.lid == right.lid) or (left.jid and left.jid == right.jid))


def receipt_time(value: int) -> str:
    if not value:
        return "Sin confirmación"
    try:
        stamp = datetime.fromtimestamp(value, UTC)
        try:
            return stamp.astimezone().strftime("%d/%m/%Y %H:%M:%S")
        except (OSError, OverflowError, ValueError):
            return stamp.strftime("%d/%m/%Y %H:%M:%S UTC")
    except (OSError, OverflowError, ValueError):
        return "Fecha no disponible"


class MessageInfoDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, loader: InfoLoader) -> None:
        super().__init__(
            parent,
            title="Información del mensaje",
            size=(900, 520),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._loader = loader
        self._active = True
        self._pending = False
        self._request_id = 0
        self._rows: tuple[ParticipantReceipt, ...] = ()
        self._view_rows: tuple[ParticipantReceipt | str, ...] = ()
        self._info: GroupMessageInfo | None = None
        self.status = wx.StaticText(self, label="Consultando al puente...")
        self.people = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.people.SetName("Leído, entregado y sin entrega confirmada: participantes del mensaje")
        for i, (title, width) in enumerate(
            (
                ("Persona", 230),
                ("Estado", 270),
                ("Entregado", 180),
                ("Leído", 180),
            )
        ):
            self.people.InsertColumn(i, title, width=width)
        self.refresh_button = wx.Button(self, label="&Actualizar")
        close_button = wx.Button(self, wx.ID_CANCEL, "&Cerrar")
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self.refresh_button, 0, wx.RIGHT, 8)
        buttons.Add(close_button)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.status, 0, wx.ALL | wx.EXPAND, 12)
        box.Add(self.people, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)
        box.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizer(box)
        self.SetMinSize((650, 350))
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda _event: self.refresh(), self.timer)
        self.Bind(wx.EVT_BUTTON, lambda _event: self.refresh(), self.refresh_button)
        self.Bind(wx.EVT_BUTTON, self._close, close_button)
        self.Bind(wx.EVT_CLOSE, self._close)
        self.Bind(wx.EVT_CHAR_HOOK, self._key)
        self.people.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._details)
        apply_theme(self)
        self.people.SetFocus()
        wx.CallAfter(self.refresh)

    def deactivate(self) -> None:
        self._active = False
        self._request_id += 1
        self.timer.Stop()

    def _close(self, _event: wx.Event) -> None:
        self.deactivate()
        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        else:
            self.Hide()

    def _key(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self._close(event)
        else:
            event.Skip()

    def refresh(self) -> None:
        if not self._active or self._pending:
            return
        self._pending = True
        self._request_id += 1
        request_id = self._request_id
        self.refresh_button.Enable(False)
        self._loader(lambda info, error, final: self._loaded(request_id, info, error, final))

    def _loaded(
        self, request_id: int, info: GroupMessageInfo | None, error: str, final: bool
    ) -> None:
        if not self._active or request_id != self._request_id:
            return
        if info is not None:
            if info != self._info:
                self._info = merge_message_info(self._info, info)
            info = self._info
        elif error and self._info is not None:
            info = self._info
        if final:
            self._pending = False
            self.refresh_button.Enable(True)
            if error:
                self.timer.Stop()
            elif not self.timer.IsRunning():
                self.timer.Start(REFRESH_INTERVAL_MS)
        if info is not None:
            rows = tuple(
                sorted(info.participants, key=lambda p: (
                    receipt_section(p), (p.name or p.key).casefold(), p.key,
                ))
            )
            if rows != self._rows or not self._view_rows:
                selected = self.people.GetFirstSelected()
                previous = (
                    self._view_rows[selected] if 0 <= selected < len(self._view_rows) else None
                )
                top = self.people.GetTopItem()
                previous_top = (
                    self._view_rows[top] if 0 <= top < len(self._view_rows) else None
                )
                selected_visible = top <= selected < top + self.people.GetCountPerPage()
                self.people.Freeze()
                try:
                    self.people.DeleteAllItems()
                    self._rows = rows
                    view_rows: list[ParticipantReceipt | str] = []
                    for section, title in enumerate(SECTION_TITLES):
                        people = [p for p in rows if receipt_section(p) == section]
                        i = self.people.InsertItem(
                            self.people.GetItemCount(), f"{title} ({len(people)})",
                        )
                        self.people.SetItemFont(i, self.people.GetFont().Bold())
                        view_rows.append(title)
                        for row in people:
                            i = self.people.InsertItem(
                                self.people.GetItemCount(), row.name or row.key,
                            )
                            self.people.SetItem(i, 1, row.state)
                            self.people.SetItem(i, 2, receipt_time(row.delivered_at))
                            self.people.SetItem(i, 3, receipt_time(row.read_at))
                            view_rows.append(row)
                    self._view_rows = tuple(view_rows)
                    target = next(
                        (
                            i
                            for i, row in enumerate(view_rows)
                            if previous is not None
                            and same_view_row(previous, row)
                        ),
                        -1,
                    )
                    if target < 0:
                        target = min(max(selected, 0), len(view_rows) - 1)
                    if target >= 0:
                        self.people.Select(target)
                        self.people.Focus(target)
                    anchor = next((
                        i for i, row in enumerate(view_rows)
                        if previous_top is not None and same_view_row(previous_top, row)
                    ), min(max(top, 0), len(view_rows) - 1))
                    self.people.EnsureVisible(anchor)
                    if selected_visible and target != selected:
                        self.people.EnsureVisible(target)
                finally:
                    self.people.Thaw()
            read = sum(bool(p.read_at) for p in rows)
            delivered = sum(bool(p.delivered_at) and not p.read_at for p in rows)
            pending = len(rows) - read - delivered
            label = (
                f"Leído: {read}. Entregado sin lectura: {delivered}. "
                f"Sin entrega confirmada: {pending}."
            )
            if not info.recipients_complete:
                label += " Lista incompleta: no se conocen todos los destinatarios originales."
            label += " La falta de un acuse no demuestra que no haya llegado."
            if not final:
                label += " Copia local; consultando al puente..."
            if error:
                label += " " + error + " Se conserva la copia local."
            self.status.SetLabel(label)
        elif error:
            self.status.SetLabel(error)

    def _details(self, _event: wx.ListEvent) -> None:
        selected = self.people.GetFirstSelected()
        if not 0 <= selected < len(self._view_rows):
            return
        row = self._view_rows[selected]
        if isinstance(row, str):
            return
        wx.MessageBox(
            f"{row.name or row.key}\n{row.state}\n"
            f"Entregado: {receipt_time(row.delivered_at)}\nLeído: {receipt_time(row.read_at)}",
            "Confirmaciones del participante",
            parent=self,
        )
