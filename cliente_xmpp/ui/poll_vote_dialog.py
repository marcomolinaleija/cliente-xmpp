from __future__ import annotations

import wx

from cliente_xmpp.ui.theme import apply_theme


class PollVoteDialog(wx.Dialog):
    """Accessible poll choices with one native control per option."""

    def __init__(
        self,
        parent: wx.Window,
        title: str,
        options: tuple[str, ...],
        selectable_count: int,
        selected: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            parent,
            title="Votar en encuesta",
            size=(600, 480),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._options = options
        self._selectable_count = max(1, min(selectable_count, len(options)))
        self._controls: list[wx.RadioButton | wx.CheckBox] = []

        heading = wx.StaticText(self, label=title)
        if self._selectable_count == 1:
            instruction_text = "Selecciona una opción."
        else:
            instruction_text = (
                f"Selecciona entre 1 y {self._selectable_count} opciones."
            )
        instruction = wx.StaticText(self, label=instruction_text)

        choices = wx.ScrolledWindow(self, style=wx.VSCROLL | wx.TAB_TRAVERSAL)
        choices.SetScrollRate(0, 12)
        choices_sizer = wx.BoxSizer(wx.VERTICAL)
        selected_set = set(selected)
        for index, option in enumerate(options):
            label = option.replace("&", "&&")
            if self._selectable_count == 1:
                style = wx.RB_GROUP if index == 0 else 0
                control: wx.RadioButton | wx.CheckBox = wx.RadioButton(
                    choices,
                    label=label,
                    style=style,
                )
                if selected:
                    control.SetValue(option in selected_set)
            else:
                control = wx.CheckBox(choices, label=label)
                control.SetValue(option in selected_set)
            control.SetName(f"Opción de encuesta: {option}")
            self._controls.append(control)
            choices_sizer.Add(control, 0, wx.ALL | wx.EXPAND, 8)
        choices.SetSizer(choices_sizer)
        choices.FitInside()

        self.validation = wx.StaticText(self, label="")
        self.validation.SetName("Estado de la selección de la encuesta")
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)

        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(heading, 0, wx.ALL | wx.EXPAND, 12)
        layout.Add(instruction, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        layout.Add(choices, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)
        layout.Add(self.validation, 0, wx.ALL | wx.EXPAND, 12)
        if buttons is not None:
            layout.Add(buttons, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        self.SetSizer(layout)
        self.SetMinSize((440, 340))

        self.Bind(wx.EVT_BUTTON, self._on_accept, id=wx.ID_OK)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)
        apply_theme(self)
        self.CentreOnParent()
        if self._controls:
            wx.CallAfter(self._controls[0].SetFocus)

    def get_selected_options(self) -> list[str]:
        return [
            option
            for option, control in zip(self._options, self._controls, strict=True)
            if control.GetValue()
        ]

    def _on_accept(self, _event: wx.CommandEvent) -> None:
        selected_count = len(self.get_selected_options())
        if selected_count < 1:
            self._show_validation("Selecciona al menos una opción.")
            return
        if selected_count > self._selectable_count:
            self._show_validation(
                f"Esta encuesta permite hasta {self._selectable_count} opciones."
            )
            return
        self.EndModal(wx.ID_OK)

    def _show_validation(self, message: str) -> None:
        self.validation.SetLabel(message)
        self.validation.SetName(f"Error: {message}")
        self.Layout()
        self.validation.SetFocus()

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()
