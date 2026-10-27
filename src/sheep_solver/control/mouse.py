"""Windows mouse control with physical-pixel region checks."""

from __future__ import annotations

import ctypes
import sys
import time
from threading import Event
from typing import Protocol

from sheep_solver.capture.screen import Region, set_dpi_awareness
from sheep_solver.planning.basic import Action


class MouseBackend(Protocol):
    def click(self, x: int, y: int) -> None: ...


class WindowsMouseBackend:
    def click(self, x: int, y: int) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Mouse click requires Windows")
        set_dpi_awareness()
        user32 = ctypes.windll.user32
        if not user32.SetCursorPos(x, y):
            raise RuntimeError("SetCursorPos failed")
        time.sleep(.05)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        time.sleep(.05)
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP


class SafeMouse:
    def __init__(self, backend: MouseBackend | None = None, *, min_interval: float = .8,
                 stop_event: Event | None = None):
        self.backend = backend or WindowsMouseBackend()
        self.min_interval = min_interval
        self.stop_event = stop_event or Event()
        self._last_click = -float("inf")

    def click(self, action: Action, game_region: Region, board_local: Region) -> None:
        if self.stop_event.is_set():
            raise RuntimeError("Emergency stop active")
        if not board_local.contains(action.local_x, action.local_y, margin=5):
            raise ValueError("Click outside calibrated board")
        expected = game_region.to_screen(action.local_x, action.local_y)
        if expected != (action.screen_x, action.screen_y) or not game_region.contains(*expected, margin=5):
            raise ValueError("Click coordinate mismatch or outside game region")
        now = time.monotonic()
        if now - self._last_click < self.min_interval:
            raise RuntimeError("Click rate limit")
        self.backend.click(*expected)
        self._last_click = time.monotonic()
