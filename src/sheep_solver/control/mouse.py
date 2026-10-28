"""Windows mouse control with physical-pixel region checks."""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from threading import Event
from typing import Protocol

from sheep_solver.capture.screen import Region, set_dpi_awareness
from sheep_solver.planning.basic import Action


class MouseBackend(Protocol):
    def click(self, x: int, y: int) -> None: ...


class _Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _InputData(ctypes.Union):
    _fields_ = [("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("data", _InputData)]


class WindowsMouseBackend:
    def __init__(self, stop_event: Event | None = None, *, user32: object | None = None,
                 expected_hwnd: int | None = None, expected_pid: int | None = None):
        self.stop_event = stop_event or Event()
        self._user32 = user32
        self.expected_hwnd = expected_hwnd
        self.expected_pid = expected_pid

    def click(self, x: int, y: int) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Mouse click requires Windows")
        set_dpi_awareness()
        user32 = self._user32 if self._user32 is not None else ctypes.windll.user32
        user32.WindowFromPoint.argtypes = [_Point]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
        user32.SendInput.restype = wintypes.UINT

        target = user32.WindowFromPoint(_Point(x, y))
        target = user32.GetAncestor(target, 2) if target else None  # GA_ROOT
        if not target:
            raise RuntimeError("No game window at click coordinate")
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(target, ctypes.byref(process_id))
        if ((self.expected_hwnd is not None and int(target) != self.expected_hwnd) or
                (self.expected_pid is not None and process_id.value != self.expected_pid)):
            raise RuntimeError("Click target is no longer the bound game window")
        if process_id.value == os.getpid():
            raise RuntimeError("Control window covers the game click coordinate")
        if user32.GetForegroundWindow() != target:
            focused = bool(user32.SetForegroundWindow(target))
            if not focused and self._user32 is None:
                # A Tk worker thread may not own the foreground input queue.
                # Attach only for the focus transfer, then always detach.
                foreground = user32.GetForegroundWindow()
                foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
                current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
                user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
                user32.AttachThreadInput.restype = wintypes.BOOL
                user32.BringWindowToTop.argtypes = [wintypes.HWND]
                user32.BringWindowToTop.restype = wintypes.BOOL
                if foreground_thread and foreground_thread != current_thread and user32.AttachThreadInput(
                        current_thread, foreground_thread, True):
                    try:
                        user32.BringWindowToTop(target)
                        user32.SetForegroundWindow(target)
                    finally:
                        user32.AttachThreadInput(current_thread, foreground_thread, False)
            if self.stop_event.wait(.05):
                raise RuntimeError("Emergency stop active")
            if user32.GetForegroundWindow() != target:
                raise RuntimeError("Could not focus game window")
        if not user32.SetCursorPos(x, y):
            raise RuntimeError("SetCursorPos failed")
        if self.stop_event.wait(.03):
            raise RuntimeError("Emergency stop active")
        down = _Input(0, _InputData(_MouseInput(0, 0, 0, 0x0002, 0, 0)))
        up = _Input(0, _InputData(_MouseInput(0, 0, 0, 0x0004, 0, 0)))
        if user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(_Input)) != 1:
            raise RuntimeError("SendInput LEFTDOWN failed")
        try:
            self.stop_event.wait(.03)
        finally:
            if user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_Input)) != 1:
                user32.mouse_event(0x0004, 0, 0, 0, 0)  # Emergency release fallback.
                raise RuntimeError("SendInput LEFTUP failed")


class SafeMouse:
    def __init__(self, backend: MouseBackend | None = None, *, min_interval: float = .25,
                 stop_event: Event | None = None):
        self.stop_event = stop_event or Event()
        self.backend = backend or WindowsMouseBackend(self.stop_event)
        self.min_interval = min_interval
        self._last_click = -float("inf")

    def click(self, action: Action, game_region: Region, board_local: Region) -> None:
        if self.stop_event.is_set():
            raise RuntimeError("Emergency stop active")
        if not board_local.contains(action.local_x, action.local_y, margin=5):
            raise ValueError("Click outside calibrated board")
        expected = game_region.to_screen(action.local_x, action.local_y)
        if expected != (action.screen_x, action.screen_y) or not game_region.contains(*expected, margin=5):
            raise ValueError("Click coordinate mismatch or outside game region")
        remaining = self.min_interval - (time.monotonic() - self._last_click)
        if remaining > 0 and self.stop_event.wait(remaining):
            raise RuntimeError("Emergency stop active")
        if self.stop_event.is_set():
            raise RuntimeError("Emergency stop active")
        self.backend.click(*expected)
        self._last_click = time.monotonic()
