from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

APP_USER_MODEL_ID = "MarcoML.WhatsAppCAN"
APP_DISPLAY_NAME = "WhatsApp CAN"
MAX_NOTIFICATION_TITLE_LENGTH = 64
MAX_NOTIFICATION_MESSAGE_LENGTH = 256
ACTION_REPLY = "reply"
ACTION_MARK_READ = "mark_read"


@dataclass(frozen=True, slots=True)
class WindowsNotificationContent:
    title: str
    message: str


def format_windows_notification(title: str, message: str) -> WindowsNotificationContent:
    return WindowsNotificationContent(
        title=_normalized_text(title, MAX_NOTIFICATION_TITLE_LENGTH) or APP_DISPLAY_NAME,
        message=_normalized_text(message, MAX_NOTIFICATION_MESSAGE_LENGTH) or "Nuevo mensaje",
    )


class WindowsNotificationService:
    def __init__(
        self,
        *,
        on_open_chat: Callable[[str], None],
        on_mark_read: Callable[[str], None],
    ) -> None:
        self._on_open_chat = on_open_chat
        self._on_mark_read = on_mark_read
        self._toaster: Any | None = None
        self._active: list[Any] = []
        self._initialized = False
        self.native_toasts_enabled = False
        self.initialization_error = ""

    def show_message(self, *, title: str, message: str, chat_jid: str) -> bool:
        self._ensure_initialized()
        if self._toaster is None:
            return False

        from windows_toasts import Toast, ToastButton

        content = format_windows_notification(title, message)
        toast: Any
        toast = Toast(
            [content.title, content.message],
            on_activated=lambda event: self._handle_activation(
                toast,
                chat_jid,
                event.arguments,
            ),
            on_failed=lambda _event: self._forget(toast),
        )
        if chat_jid:
            toast.AddAction(ToastButton("Responder", ACTION_REPLY))
            toast.AddAction(ToastButton("Marcar como leído", ACTION_MARK_READ))

        self._active.append(toast)
        self._active = self._active[-100:]
        try:
            self._toaster.show_toast(toast)
        except Exception:
            self._forget(toast)
            return False
        return True

    def close_all(self) -> None:
        self._active.clear()

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        if sys.platform != "win32":
            self.initialization_error = "Las notificaciones requieren Windows 10 u 11."
            return

        try:
            _register_app_user_model_id()
            from windows_toasts import InteractableWindowsToaster

            self._toaster = InteractableWindowsToaster(
                APP_DISPLAY_NAME,
                APP_USER_MODEL_ID,
            )
        except Exception as exc:
            self.initialization_error = str(exc)
            return

        self.native_toasts_enabled = True

    def _handle_activation(
        self,
        toast: Any,
        chat_jid: str,
        action: str | None,
    ) -> None:
        self._forget(toast)
        if not chat_jid:
            return
        if action == ACTION_MARK_READ:
            self._on_mark_read(chat_jid)
            return
        self._on_open_chat(chat_jid)

    def _forget(self, toast: Any) -> None:
        self._active = [current for current in self._active if current is not toast]


def _normalized_text(value: str, max_length: int) -> str:
    value = " ".join(str(value or "").split())
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3]}..."


def _register_app_user_model_id() -> None:
    import winreg

    key_path = rf"SOFTWARE\Classes\AppUserModelId\{APP_USER_MODEL_ID}"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_DISPLAY_NAME)
