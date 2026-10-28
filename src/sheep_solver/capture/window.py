"""Read-only Windows window discovery and client-area coordinates."""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass

from .screen import Region, set_dpi_awareness


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    client: Region


def windows() -> tuple[WindowInfo, ...]:
    if sys.platform != "win32":
        return ()
    set_dpi_awareness()
    user32 = ctypes.windll.user32
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]

    @callback_type
    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length < 2:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        rect = wintypes.RECT()
        point = wintypes.POINT(0, 0)
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)) or not user32.ClientToScreen(hwnd, ctypes.byref(point)):
            return True
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 320 or height < 500:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != os.getpid():
            found.append(WindowInfo(int(hwnd), pid.value, title.value,
                                    Region(point.x, point.y, width, height)))
        return True

    user32.EnumWindows(visit, 0)
    return tuple(found)


def current_client(hwnd: int, pid: int) -> Region | None:
    return next((item.client for item in windows() if item.hwnd == hwnd and item.pid == pid), None)


def window_for_region(region: Region) -> WindowInfo | None:
    """Bind a user-selected game area to the smallest containing client window."""
    center_x = region.left + region.width // 2
    center_y = region.top + region.height // 2
    candidates = [item for item in windows() if
                  item.client.contains(center_x, center_y) and
                  item.client.left <= region.left and item.client.top <= region.top and
                  item.client.right >= region.right and item.client.bottom >= region.bottom]
    candidates.sort(key=lambda item: item.client.width * item.client.height)
    if len(candidates) > 1 and (candidates[0].client.width * candidates[0].client.height >=
                                .9 * candidates[1].client.width * candidates[1].client.height):
        return None
    return candidates[0] if candidates else None


def focus_window(hwnd: int, pid: int) -> bool:
    """Bring an already bound window forward before screen capture."""
    if sys.platform != "win32" or current_client(hwnd, pid) is None:
        return False
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    if user32.GetForegroundWindow() == hwnd:
        return True
    user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() == hwnd:
        return True
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    if foreground_thread and foreground_thread != current_thread and user32.AttachThreadInput(
            current_thread, foreground_thread, True):
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            user32.AttachThreadInput(current_thread, foreground_thread, False)
    return user32.GetForegroundWindow() == hwnd
