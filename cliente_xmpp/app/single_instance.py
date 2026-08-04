from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

_ERROR_ALREADY_EXISTS = 183
_WAIT_OBJECT_0 = 0
_ASFW_ANY = -1


class SingleInstanceGuard:
    """Coordinate one WhatsApp CAN window per Windows user session."""

    def __init__(
        self,
        mutex_name: str = r"Local\WhatsAppCAN.SingleInstance",
        activation_event_name: str = r"Local\WhatsAppCAN.ActivateExistingInstance",
    ) -> None:
        self._mutex_name = mutex_name
        self._activation_event_name = activation_event_name
        self._mutex: wintypes.HANDLE | None = None
        self._activation_event: wintypes.HANDLE | None = None

        if os.name != "nt":
            raise OSError("WhatsApp CAN solo admite Windows.")

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._configure_winapi()

    def _configure_winapi(self) -> None:
        self._kernel32.CreateMutexW.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        self._kernel32.CreateMutexW.restype = wintypes.HANDLE
        self._kernel32.CreateEventW.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        self._kernel32.CreateEventW.restype = wintypes.HANDLE
        self._kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
        self._kernel32.SetEvent.restype = wintypes.BOOL
        self._kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self._kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._user32.AllowSetForegroundWindow.argtypes = (wintypes.DWORD,)
        self._user32.AllowSetForegroundWindow.restype = wintypes.BOOL

    def acquire(self) -> bool:
        """Return True for the primary instance and False for a later launch."""
        if self._mutex is not None:
            raise RuntimeError("La instancia única ya fue inicializada.")

        ctypes.set_last_error(0)
        mutex = self._kernel32.CreateMutexW(None, False, self._mutex_name)
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        another_instance_exists = ctypes.get_last_error() == _ERROR_ALREADY_EXISTS

        activation_event = self._kernel32.CreateEventW(
            None,
            False,
            False,
            self._activation_event_name,
        )
        if not activation_event:
            self._kernel32.CloseHandle(mutex)
            raise ctypes.WinError(ctypes.get_last_error())

        self._mutex = mutex
        self._activation_event = activation_event
        return not another_instance_exists

    def request_activation(self) -> bool:
        """Ask the primary instance to restore and activate its main window."""
        if self._activation_event is None:
            raise RuntimeError("La instancia única no fue inicializada.")

        # The user started this process, so pass the foreground permission to
        # the primary process before it restores its own wx window.
        self._user32.AllowSetForegroundWindow(_ASFW_ANY)
        return bool(self._kernel32.SetEvent(self._activation_event))

    def consume_activation_request(self) -> bool:
        """Consume one pending activation request without blocking the UI thread."""
        if self._activation_event is None:
            return False
        return self._kernel32.WaitForSingleObject(self._activation_event, 0) == _WAIT_OBJECT_0

    def close(self) -> None:
        """Release local handle references; Windows releases the mutex on exit too."""
        if self._activation_event is not None:
            self._kernel32.CloseHandle(self._activation_event)
            self._activation_event = None
        if self._mutex is not None:
            self._kernel32.CloseHandle(self._mutex)
            self._mutex = None
