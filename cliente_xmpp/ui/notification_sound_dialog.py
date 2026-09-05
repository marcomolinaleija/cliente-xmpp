from __future__ import annotations

import os
import shutil
from pathlib import Path

import wx

from cliente_xmpp.audio.notification import play_sound_path
from cliente_xmpp.config.settings import APP_DIR

SUPPORTED_NOTIFICATION_SOUND_SUFFIXES = frozenset(
    {".wav", ".mp3", ".m4a", ".wma", ".ogg", ".flac"}
)
CUSTOM_NOTIFICATION_SOUNDS_DIR = APP_DIR / "notification_sounds"


class NotificationSoundDialog(wx.Dialog):
    """Accessible picker with automatic preview for Windows and imported sounds."""

    def __init__(self, parent: wx.Window, *, selected_path: str = "") -> None:
        super().__init__(parent, title="Explorar sonidos de notificación", size=(620, 440))
        self.selected_path = ""
        self._paths: list[Path] = []

        description = wx.StaticText(
            self,
            label=(
                "Selecciona un sonido. Al cambiar de fila se reproduce una preescucha. "
                "La lista incluye los sonidos de Windows y los archivos agregados aquí."
            ),
        )
        self.sound_list = wx.ListBox(self)
        self.sound_list.SetName("Sonidos disponibles")
        self.add_button = wx.Button(self, label="Agregar archivo...")
        self.use_button = wx.Button(self, wx.ID_OK, "Usar este sonido")
        cancel_button = wx.Button(self, wx.ID_CANCEL, "Cancelar")

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self.add_button, 0, wx.RIGHT, 8)
        buttons.AddStretchSpacer(1)
        buttons.Add(self.use_button, 0, wx.RIGHT, 8)
        buttons.Add(cancel_button, 0)
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(description, 0, wx.ALL | wx.EXPAND, 12)
        layout.Add(self.sound_list, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        layout.Add(buttons, 0, wx.ALL | wx.EXPAND, 12)
        self.SetSizerAndFit(layout)
        self.SetMinSize((520, 360))

        self.sound_list.Bind(wx.EVT_LISTBOX, self._on_sound_selected)
        self.add_button.Bind(wx.EVT_BUTTON, self._on_add_file)
        self.Bind(wx.EVT_BUTTON, self._on_use_selected, self.use_button)
        self._load_sounds(selected_path)

    def _load_sounds(self, selected_path: str = "") -> None:
        selected_normalized = str(Path(selected_path)).casefold() if selected_path else ""
        self._paths = _available_sound_paths()
        labels = [_sound_label(path) for path in self._paths]
        self.sound_list.Set(labels)
        self.use_button.Enable(bool(self._paths))
        if not self._paths:
            return
        selected_index = next(
            (
                index
                for index, path in enumerate(self._paths)
                if str(path).casefold() == selected_normalized
            ),
            0,
        )
        self.sound_list.SetSelection(selected_index)

    def _on_sound_selected(self, _event: wx.CommandEvent) -> None:
        index = self.sound_list.GetSelection()
        if index == wx.NOT_FOUND:
            return
        play_sound_path(self._paths[index], "cliente_xmpp_sound_browser_preview")

    def _on_add_file(self, _event: wx.CommandEvent) -> None:
        with wx.FileDialog(
            self,
            "Agregar sonido de notificación",
            wildcard=(
                "Archivos de audio (*.wav;*.mp3;*.m4a;*.wma;*.ogg;*.flac)|"
                "*.wav;*.mp3;*.m4a;*.wma;*.ogg;*.flac"
            ),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            source = Path(dialog.GetPath())
        try:
            imported = _import_sound(source)
        except OSError as exc:
            wx.MessageBox(
                f"No se pudo agregar el sonido: {exc}",
                "Sonidos de notificación",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        self._load_sounds(str(imported))
        index = self.sound_list.GetSelection()
        if index != wx.NOT_FOUND:
            play_sound_path(self._paths[index], "cliente_xmpp_sound_browser_preview")

    def _on_use_selected(self, event: wx.CommandEvent) -> None:
        index = self.sound_list.GetSelection()
        if index == wx.NOT_FOUND:
            return
        self.selected_path = str(self._paths[index])
        event.Skip()


def _available_sound_paths() -> list[Path]:
    windows_media = Path(os.environ.get("WINDIR", r"C:\\Windows")) / "Media"
    paths: list[Path] = []
    for directory in (windows_media, CUSTOM_NOTIFICATION_SOUNDS_DIR):
        if not directory.is_dir():
            continue
        paths.extend(
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.casefold() in SUPPORTED_NOTIFICATION_SOUND_SUFFIXES
        )
    return sorted(set(paths), key=lambda path: (_sound_label(path).casefold(), str(path)))


def _import_sound(source: Path) -> Path:
    if source.suffix.casefold() not in SUPPORTED_NOTIFICATION_SOUND_SUFFIXES:
        raise OSError("El archivo no es un formato de audio compatible.")
    CUSTOM_NOTIFICATION_SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    destination = CUSTOM_NOTIFICATION_SOUNDS_DIR / source.name
    suffix = 2
    while destination.exists() and not destination.samefile(source):
        destination = CUSTOM_NOTIFICATION_SOUNDS_DIR / f"{source.stem} ({suffix}){source.suffix}"
        suffix += 1
    if not destination.exists():
        shutil.copy2(source, destination)
    return destination


def _sound_label(path: Path) -> str:
    source = "Windows" if path.parent.name.casefold() == "media" else "Mis sonidos"
    return f"{path.stem} ({source})"
