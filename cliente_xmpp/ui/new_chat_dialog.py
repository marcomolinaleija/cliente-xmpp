from __future__ import annotations

import wx

from cliente_xmpp.models.phone_numbers import (
    CountryDialingOption,
    NormalizedPhoneNumber,
    PhoneNumberError,
    country_dialing_options,
    normalize_phone_number,
)


class NewChatDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, default_region: str = "MX") -> None:
        super().__init__(parent, title="Nuevo chat")

        self._countries = country_dialing_options()
        self._normalized_phone: NormalizedPhoneNumber | None = None

        self.instructions = wx.StaticText(
            self,
            label=(
                "Selecciona un país y escribe el número con lada o código de área. "
                "También puedes pegar un número internacional que empiece con + o 00."
            ),
        )
        self.country_label = wx.StaticText(self, label="País o región:")
        self.country = wx.ComboBox(
            self,
            choices=[option.label for option in self._countries],
            style=wx.CB_READONLY,
        )
        self.country.SetName("País o región")
        self.country.SetToolTip(
            "Selecciona el país o región. Escribe las primeras letras para encontrarlo."
        )
        self.number_label = wx.StaticText(self, label="Número:")
        self.number = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.number.SetName("Número de teléfono")
        self.number.SetHint("Ejemplo: 449 123 4567")
        self.number.SetToolTip(
            "Escribe el número nacional o pega el número internacional completo."
        )
        self.preview = wx.StaticText(self, label="Destinatario: pendiente")
        self.preview.SetName("Vista previa del destinatario")
        self.error = wx.StaticText(self, label="")
        self.error.SetName("Error del número")
        self.open_button = wx.Button(self, wx.ID_OK, "Abrir chat")
        self.cancel_button = wx.Button(self, wx.ID_CANCEL, "Cancelar")

        self._select_region(default_region)
        self._layout()
        self._bind_events()
        self.open_button.SetDefault()
        self.SetEscapeId(wx.ID_CANCEL)
        self.SetMinSize((560, -1))
        wx.CallAfter(self.number.SetFocus)

    @property
    def selected_region(self) -> str:
        option = self._selected_country()
        return option.region_code if option is not None else "MX"

    @property
    def normalized_phone(self) -> NormalizedPhoneNumber | None:
        return self._normalized_phone

    def _layout(self) -> None:
        fields = wx.FlexGridSizer(cols=2, hgap=10, vgap=10)
        fields.AddGrowableCol(1, 1)
        fields.Add(self.country_label, 0, wx.ALIGN_CENTER_VERTICAL)
        fields.Add(self.country, 1, wx.EXPAND)
        fields.Add(self.number_label, 0, wx.ALIGN_CENTER_VERTICAL)
        fields.Add(self.number, 1, wx.EXPAND)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer(1)
        buttons.Add(self.open_button, 0, wx.ALL, 6)
        buttons.Add(self.cancel_button, 0, wx.ALL, 6)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.instructions, 0, wx.ALL | wx.EXPAND, 12)
        box.Add(fields, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        box.Add(self.preview, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        box.Add(self.error, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        box.Add(buttons, 0, wx.EXPAND)
        self.SetSizerAndFit(box)

    def _bind_events(self) -> None:
        self.country.Bind(wx.EVT_COMBOBOX, self._on_value_changed)
        self.number.Bind(wx.EVT_TEXT, self._on_value_changed)
        self.number.Bind(wx.EVT_TEXT_ENTER, self._on_open)
        self.open_button.Bind(wx.EVT_BUTTON, self._on_open)

    def _on_value_changed(self, event: wx.CommandEvent) -> None:
        self._refresh_preview()
        event.Skip()

    def _on_open(self, _event: wx.CommandEvent) -> None:
        option = self._selected_country()
        if option is None:
            self._show_error("Selecciona un país o región.", focus_country=True)
            return

        try:
            normalized = normalize_phone_number(self.number.GetValue(), option.region_code)
        except PhoneNumberError as exc:
            self._show_error(str(exc))
            return

        self._normalized_phone = normalized
        self.error.SetLabel("")
        self.EndModal(wx.ID_OK)

    def _refresh_preview(self) -> None:
        option = self._selected_country()
        if option is None or not self.number.GetValue().strip():
            self.preview.SetLabel("Destinatario: pendiente")
            self._normalized_phone = None
            return

        try:
            normalized = normalize_phone_number(self.number.GetValue(), option.region_code)
        except PhoneNumberError:
            self.preview.SetLabel("Destinatario: pendiente")
            self._normalized_phone = None
            return

        self._normalized_phone = normalized
        self.preview.SetLabel(f"Destinatario: {normalized.international}")
        self.error.SetLabel("")
        self.number.SetName("Número de teléfono")
        self.Layout()

    def _show_error(self, message: str, *, focus_country: bool = False) -> None:
        self.error.SetLabel(message)
        self.Layout()
        if focus_country:
            self.country.SetName(f"País o región. Error: {message}")
            self.country.SetFocus()
            return

        self.number.SetName(f"Número de teléfono. Error: {message}")
        self.number.SetFocus()
        self.number.SelectAll()

    def _select_region(self, region_code: str) -> None:
        region_code = region_code.strip().upper()
        default_index = next(
            (
                index
                for index, option in enumerate(self._countries)
                if option.region_code == "MX"
            ),
            0,
        )
        index = next(
            (
                index
                for index, option in enumerate(self._countries)
                if option.region_code == region_code
            ),
            default_index,
        )
        self.country.SetSelection(index)

    def _selected_country(self) -> CountryDialingOption | None:
        index = self.country.GetSelection()
        if index == wx.NOT_FOUND or index >= len(self._countries):
            return None
        return self._countries[index]
