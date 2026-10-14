from __future__ import annotations

import re

import wx

EMOTICONS = {
    ":)": "🙂", ":-)": "🙂",
    ":(": "🙁", ":-(": "🙁",
    ";)": "😉", ";-)": "😉",
    ":D": "😃", ":-D": "😃",
    "xD": "😆", "XD": "😆",
    ":P": "😛", ":p": "😛", ":-P": "😛", ":-p": "😛",
    ";P": "😜", ";p": "😜", ";-P": "😜", ";-p": "😜",
    ":O": "😮", ":o": "😮", ":-O": "😮", ":-o": "😮",
    ":'(": "😢", ":'-(": "😢",
    ";(": "😢", ";-(": "😢",
    ":*": "😘", ":-*": "😘",
    "8)": "😎", "8-)": "😎",
    ":|": "😐", ":-|": "😐",
    ":/": "😕", ":-/": "😕",
    "<3": "❤️", "</3": "💔",
}
MAX_EMOTICON_LENGTH = max(map(len, EMOTICONS))


def convert_standalone_emoticon(text: str) -> str:
    """Convert only a complete message consisting of one recognized emoticon."""
    return EMOTICONS.get(text, text)


def _token_before_space(before: str, after: str) -> str:
    if after and not after[0].isspace():
        return ""
    return before.rsplit(maxsplit=1)[-1] if before and not before[-1].isspace() else ""


def emoticon_before_space(before: str, after: str = "") -> tuple[str, str] | None:
    """Match a complete token at the caret, outside backtick-delimited text."""
    token = _token_before_space(before, after)
    emoji = EMOTICONS.get(token)
    if emoji is None:
        return None
    delimiter = 0
    for match in re.finditer(r"`+", before):
        length = len(match[0])
        if delimiter == 0:
            delimiter = length
        elif length == delimiter:
            delimiter = 0
    return None if delimiter else (token, emoji)


class EmoticonTextCtrl(wx.TextCtrl):
    """Convert only a typed space; keep paste, undo and native positions intact."""

    def __init__(self, parent: wx.Window, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self.Bind(wx.EVT_CHAR, self._on_char)

    def _on_char(self, event: wx.KeyEvent) -> None:
        if (
            event.GetUnicodeKey() != ord(" ")
            or event.ControlDown() or event.AltDown() or event.MetaDown()
            or not self.IsEditable()
        ):
            event.Skip()
            return
        start, end = self.GetSelection()
        if start != end:
            event.Skip()
            return
        # wx positions on Windows are native UTF-16 positions, not Python offsets.
        tail = self.GetRange(max(0, end - MAX_EMOTICON_LENGTH - 1), end)
        after = self.GetRange(end, min(end + 1, self.GetLastPosition()))
        if _token_before_space(tail, after) not in EMOTICONS:
            event.Skip()
            return
        replacement = emoticon_before_space(self.GetRange(0, end), after)
        if replacement is None:
            event.Skip()
            return
        token, emoji = replacement
        self.replace_text_range(end - len(token), end, emoji + " ")

    def replace_text_range(self, start: int, end: int, text: str) -> None:
        """Replace native positions as one undoable edit and notify the final text."""
        old_length = self.GetLastPosition()
        # Replace() removes and inserts separately on MSW, losing the token on undo.
        # WriteText() is undoable, but its native notification can see partial text.
        blocker = wx.EventBlocker(self, wx.wxEVT_TEXT)
        try:
            self.SetSelection(start, end)
            self.WriteText(text)
            self.SetInsertionPoint(end + self.GetLastPosition() - old_length)
        finally:
            del blocker
        changed = wx.CommandEvent(wx.wxEVT_TEXT, self.GetId())
        changed.SetEventObject(self)
        changed.SetString(self.GetValue())
        self.GetEventHandler().ProcessEvent(changed)
